import dash
from dash import dcc, html, Input, Output
import plotly.graph_objs as go
from plotly.subplots import make_subplots

import serial
import serial.tools.list_ports
import threading
from collections import deque
import numpy as np

"""
we are required to do manual filtering and peak detection
"""
# signal filtering functions, consult the notebook for more details
def convolve(signal, filter:list=[1, 1]):
    """
    Docstring for convolve
        we are doing the manual way of np.convolve(signal, filter / np.sum(filter), mode='same')
        
    :param signal: signal
    :param filter: Defaults to [1, 1, 1] moving average.
    :type filter: list
    """
    
    signal = np.array(signal)
    filter = np.array(filter)
    filter_length = len(filter)
    signal_length = len(signal)
    
    # safety fallback
    if signal_length < filter_length:
        return signal
    
    filter_sum = np.sum(np.abs(filter))
    padded_signal = np.pad(signal, (filter_length, 0), mode='edge')
    
    filtered_signal = np.zeros(signal_length)
    
    for i in range(signal_length):
        window = padded_signal[i : i + filter_length]
        filtered_signal[i] = np.dot(window, filter) / filter_sum

    return filtered_signal[filter_length:]
  
def peak_detector(signal, min_time = 30, min_threshold= 1):
    """
    Docstring for peak_detector
        idea is to find local maxima and filter out based on time duration between RR
    
    :param signal: signal
    :param min_time: minimum distance between peaks
    :param min_threshold: minimum peak value
    """
   
    indices = []
    values = []
    for i in range(1, len(signal)-1):
        if signal[i] < min_threshold:
            continue
        if (signal[i] > signal[i-1]) and (signal[i] > signal[i+1]):
            # local maxima is found
            # check the last beat time
            if len(indices) > 0:
                last_beat_time = indices[-1] 
                current_beat_time = i 
                if current_beat_time - last_beat_time > min_time:
                    indices.append(i)
                    values.append(signal[i])
            else:
                indices.append(i)
                values.append(signal[i])
    return indices, values


"""
Frontend + Serial Port Communication
Code is Mostly AI generated, and manually finetuned to application needs.
"""

# --- Serial Setup ---
def select_port():
    ports = list(serial.tools.list_ports.comports())
    for i, p in enumerate(ports): print(f"[{i}] {p.device}")
    sel = int(input("Select Port: "))
    return ports[sel].device

SERIAL_PORT = select_port()
BAUD_RATE = 115200

# Buffer to store the last data points
data_buffer = deque(maxlen=150)
filtered_data = [50]

    
def serial_reader():
    global filtered_data
    """Background thread to read serial data without blocking the web server."""
    with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1) as ser:
        while True:
            line = ser.readline().decode('utf-8').strip()
            if line:
                try:
                    data_buffer.append(float(line))
                except ValueError:
                    pass
    
# Start the serial thread
thread = threading.Thread(target=serial_reader, daemon=True)
thread.start()

# --- Dash App Setup ---
app = dash.Dash(__name__)

# Clinical Theme Constants
BG_COLOR = "#fefefe"  
GRID_COLOR = "#D9D2CC" 
RAW_COLOR = "#8E8E8E"  
FILTERED_COLOR = "#56473B" 
FONT_STYLE = {"fontFamily": "Segoe UI, Helvetica, sans-serif", "color": "black"}

app.layout = html.Div(style={'backgroundColor': BG_COLOR, 'padding': '10px', 'height': '100vh'}, children=[
    html.H2("Serial Monitor - MAX30102", style={'textAlign': 'left', **FONT_STYLE, 'margin-bottom': '5px'}),
    
    html.Div([
        dcc.Graph(id='live-monitor', animate=False, config={'displayModeBar': False}),
    ]),

    dcc.Interval(id='graph-update', interval=250, n_intervals=0),
])

@app.callback(Output('live-monitor', 'figure'),
              [Input('graph-update', 'n_intervals')])
def update_combined_monitor(n):
    raw_list = np.array(list(data_buffer))
    if len(raw_list) < 10: return go.Figure()

    filtered = convolve(raw_list, filter=[1, 0, -1])
    filtered = convolve(filtered, filter=[1, 1, 1, 1, 1])
    max_value = np.max(filtered) if len(filtered) > 0 else 0
    beat_indices, beat_values = peak_detector(filtered,min_time=20, min_threshold=max_value*0.2)

    
    fig = make_subplots(
        rows=2, cols=1, 
        shared_xaxes=True, 
        vertical_spacing=0.05,
        subplot_titles=("Filtered Signal", "RAW SIGNAL"),
        row_heights=[0.8, 0.2]
    )
    
    fig.add_trace(go.Scatter(
        y=filtered,
        name='Cleaned',
        mode='lines',
        line=dict(color=FILTERED_COLOR, width=2)
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=beat_indices,
        y=beat_values, 
        mode='markers',
        marker=dict(color='red', size=20, symbol='circle-dot',opacity=0.7),
        name='Heartbeat'
    ), row=1, col=1)
    
    fig.add_trace(go.Scatter(
        y=raw_list,
        name='Raw IR',
        mode='lines',
        line=dict(color=RAW_COLOR, width=1),
    ), row=2, col=1)

    fig.update_layout(
        height=1000,
        showlegend=False,
        plot_bgcolor=BG_COLOR,
        paper_bgcolor=BG_COLOR,
        font=dict(color="black"),
        margin=dict(l=50, r=20, t=50, b=50)
    )

    # Grid and Axis Styling
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor=GRID_COLOR, zeroline=False)
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor=GRID_COLOR, zeroline=False)

    # Dynamic Y-Axis Scaling (Auto-zoom on signal)
    fig.update_yaxes(range=[np.min(filtered)-5, np.max(filtered)+5], row=1, col=1)
    fig.update_yaxes(range=[np.min(raw_list)-5, np.max(raw_list)+5], row=2, col=1)

    return fig

if __name__ == '__main__':
    app.run(debug=False)