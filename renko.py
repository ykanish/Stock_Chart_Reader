import datetime
import sys
import threading
import time
import matplotlib
from numpy import e
matplotlib.use('QT5Agg')
from PyQt5.QtCore import QPropertyAnimation, QEasingCurve, Qt, QTimer
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QMessageBox, QGridLayout, QHBoxLayout, QRadioButton,QButtonGroup , QWidget, QPushButton, QLineEdit, QComboBox, QLabel, QSpinBox
from PyQt5.QtGui import QFont
from ibapi.common import BarData
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from matplotlib import pyplot as plt
from ibapi.order import Order
import mplfinance as mpf
import pandas as pd
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

port = 7497

# Define the style for mplfinance plots
style = {
    'base_mpl_style': 'fast',
    'marketcolors': {
        'candle': {'up': '#00b060', 'down': '#fe3032'},
        'edge': {'up': '#00b060', 'down': '#fe3032'},
        'wick': {'up': '#606060', 'down': '#606060'},
        'ohlc': {'up': '#00b060', 'down': '#fe3032'},
        'volume': {'up': '#4dc790', 'down': '#fd6b6c'},
        'alpha': 0.9,
    },
        'vcdopcod': True,
        'alpha': 0.9,
        'mavcolors': None,
        'facecolor': '#fafafa',
        'gridcolor': '#d0d0d0',
        'gridstyle': '-',
        'y_on_right': False,
        'rc': {
        'axes.labelcolor': '#101010',
        'axes.edgecolor': 'f0f0f0',
        'axes.grid.axis': 'y',
        'ytick.color': '#101010',
        'xtick.color': '#101010',
        'figure.titlesize': 'small',
        'figure.titleweight': 'semibold',
        'axes.titlesize': 16,
        'axes.labelsize': 0,
        'xtick.labelsize': 'x-small',
        'ytick.labelsize': 'x-small',
        'figure.titlesize': 20
    },
    'base_mpf_style': 'yahoo'
}

time_frame_durations = {
    "1 sec": "1020 S",
    "5 secs": "7200 S",
    "15 secs": "18000 S",
    "30 secs": "16400 S",
    "1 min": "1 D",
    "2 mins": "2 D",
    "3 mins": "3 D",
    "5 mins": "5 D",
    "15 mins": "15 D",
    "30 mins": "30 D",
    "1 hour": "42 D",
    "1 day": "3 Y"
}

# TWS API client setup for real-time data fetching
class TWSClient(EWrapper, EClient):
    def __init__(self, data_dict, canvas: FigureCanvas, brick_size):
        EClient.__init__(self, self)
        self.data_dict = data_dict
        self.canvas = canvas
        self.df = pd.DataFrame(columns=['Date', 'Close'])
        self.brick_size = float(brick_size)
        self.df = pd.DataFrame()
        self.previous_brick_color = None
        self.order_id = 0
        self.nextOrderId = None
        self.contract = None
        self.order_stack = []   # Order stack to limit the orders
        self.trading = False
        self.reqid = 1
        self.active_req_ids = set()  # Set to track active request IDs
        self.intial_order_placed = False
        self.input_quantity = None
        self.last_renko_brick = None
        self.renko_bricks = []
        self.buy_only = False
        self.sell_only = False
        self.all_buy_sell = True 

    # To handle the duplicate order id
    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.nextOrderId = orderId

    def historicalData(self, reqId, bar):
        self.data_dict[bar.date.replace(" US/Eastern", "")] = [bar.open, bar.high, bar.low, bar.close]

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        self.df = pd.DataFrame.from_dict(self.data_dict, orient='index', columns=['Open', 'High', 'Low', 'Close'])
        self.df.index = pd.to_datetime(self.df.index, format="%Y%m%d %H:%M:%S")
        
        # Clear the previous plot
        self.canvas.figure.axes[0].clear()

        # Store Renko data in retvals
        retvals = {}
        mpf.plot(self.df.iloc[-500:], type='renko', scale_padding={'left': 1, 'top': 5, 'right': 1, 'bottom': 1},
                 ax=self.canvas.figure.axes[0], renko_params={'brick_size': 'atr', 'atr_length': int(self.brick_size)},
                 style=style, tight_layout=True, tz_localize=False, return_calculated_values=retvals)
        
        # Access the Renko brick values
        renko_bricks = retvals.get('renko_bricks', [])

        # Check for new Renko brick formation and give signals
        self.check_new_brick(renko_bricks)

        # Update the plot
        self.canvas.figure.get_axes()[0].yaxis.set_label_position("right")
        self.canvas.figure.get_axes()[0].yaxis.tick_right()
        self.canvas.figure.get_axes()[0].set_ylabel("")
        self.canvas.figure.get_axes()[0].set_xlabel("")
        self.canvas.draw()

    def historicalDataUpdate(self, reqId: int, bar: BarData):
        self.df.loc[pd.to_datetime(bar.date.replace(" US/Eastern", "")), :] = [bar.open, bar.high, bar.low, bar.close]
        
        # Clear the previous plot
        self.canvas.figure.axes[0].clear()

        # Store Renko data in retvals
        retvals = {}
        mpf.plot(self.df.iloc[-500:], type='renko', scale_padding={'left': 1, 'top': 5, 'right': 1, 'bottom': 1},
                 renko_params={'brick_size': 'atr', 'atr_length': int(self.brick_size)}, ax=self.canvas.figure.axes[0],
                 style=style, tz_localize=False, tight_layout=True, return_calculated_values=retvals)
        
        # Access the Renko brick values
        renko_bricks = retvals.get('renko_bricks', [])

        # Check for new Renko brick formation and give signals
        self.check_new_brick(renko_bricks)

        # Update the plot
        self.canvas.figure.get_axes()[0].yaxis.set_label_position("right")
        self.canvas.figure.get_axes()[0].yaxis.tick_right()
        self.canvas.figure.get_axes()[0].set_ylabel("")
        self.canvas.figure.get_axes()[0].set_xlabel("")
        self.canvas.draw()

    def check_new_brick(self, renko_bricks):
        """
        Check for new Renko bricks and generate trading signals accordingly.
        """
        if len(renko_bricks) < 2:
            return  # Not enough data to compare

        # Get the latest Renko brick
        current_brick = renko_bricks[-1]

        # Check if a new Renko brick has formed
        if self.last_renko_brick is None or current_brick != self.last_renko_brick:
            self.last_renko_brick = current_brick  # Update the last brick
            
            # Determine the signal based on the direction of the new brick
            action = "BUY" if current_brick > renko_bricks[-2] else "SELL"

            self.handle_order(action)  # Place the order based on the new brick
        else:
            pass
            
            
    def handle_order(self, action):
        if not self.trading:
            return

        if not (self.nextOrderId or self.contract):
            return

        if not self.order_stack:
            if (self.buy_only and action == 'SELL') or (self.sell_only and action == 'BUY'):
              
                return
        
            try:
                self.place_order(action, self.nextOrderId, self.contract, Order())
                self.order_stack.append(action)
               
            except Exception as e:
                msg_box = QMessageBox()
                msg_box.setIcon(QMessageBox.Information)
                msg_box.setText(e)
                msg_box.setWindowTitle("ERROR")
                msg_box.setStandardButtons(QMessageBox.Ok)
                msg_box.exec()    
        else:
            last_order = self.order_stack[-1]
            if last_order == action:
                return
    
                
            try:
                self.place_order(action, self.nextOrderId, self.contract, Order())
                self.order_stack.append(action)
            except Exception as e:
                msg_box = QMessageBox()
                msg_box.setIcon(QMessageBox.Information)
                msg_box.setText(e)
                msg_box.setWindowTitle("ERROR")
                msg_box.setStandardButtons(QMessageBox.Ok)
                msg_box.exec()

        # todo: check if order stack if more then 2 that mean somethig is wrong show error and stop trading 
        if len(self.order_stack)>=2 and self.order_stack[-1] != self.order_stack[-2]:
            self.order_stack.clear()
            
        
    def place_order(self, action, order_id, contract, order):
        
        if not (self.trading or self.nextOrderId or contract):
            return
        order.action = action
        order.orderType = "MKT"
        order.outsideRth = True 
        if self.input_quantity and self.input_quantity.text().strip():
            quantity = self.input_quantity.text().strip()
        else:
            msg_box = QMessageBox()
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setText("Default Value")
            msg_box.setWindowTitle("Quantity Information")
            msg_box.setStandardButtons(QMessageBox.Ok)
            msg_box.exec()    
            quantity = 500
               
        
        if contract.secType == "CRYPTO":
            
            if action == "BUY":
                order.totalQuantity = ""
                order.cashQty = quantity
            else:
                order.totalQuantity = quantity
                order.cashQty = ""
            order.tif = "IOC"  
        else:
            order.totalQuantity = 1  
            order.cashQty = ""
            order.tif = "DAY"  
            
        try:
            self.placeOrder(self.nextOrderId,contract,order)
            self.nextOrderId +=1
        except Exception as e:
            print(f"Error placing order: {str(e)}")

        self.placeOrder(order_id, contract, order)
        self.nextOrderId += 1

    def error(self, reqId, errorCode, errorString, error=""):
        print("Error: ", reqId, " ", errorCode, " ", errorString)

    def run_loop(self):
        self.run()


# Stock chart widget that handles plotting
class StockChartWidget(QWidget):
    def __init__(self, parent=None, index=1):
        super().__init__(parent)
        self.data_dict = {}
        self.canvas = FigureCanvas(mpf.figure(style=style, figsize=(8, 8), tight_layout=True))
        self.canvas.figure.add_subplot(111)
        self.tws_client = TWSClient(self.data_dict, self.canvas, 0)
        self.initUI(index)
        self.canvas.figure.subplots_adjust(left=0.1, right=0.9, bottom=0.2, top=0.9)
        self.contract = None   
        self.trading = False 
        
        self.timer = QTimer(self)  # Initialize QTimer
        self.timer.timeout.connect(self.start_data_fetch)  # Connect QTimer to data fetch method
        self.order_history = []
        self.first_order = True

    def initUI(self, index):
        layout = QVBoxLayout()
        self.title = QLabel()
        self.title.setFont(QFont('Arial', 16, QFont.Bold))
        self.title.setStyleSheet("color: green; background-color: black; padding: 10px;")
        self.title.setGeometry(0, 0, layout.geometry().width(), 60)
        layout.addWidget(self.title)
        layout.addWidget(self.canvas)
        self.canvas.draw()

        form_layout = QGridLayout()
        self.input_symbol = QLineEdit(self)
        self.input_symbol.setPlaceholderText("Enter Symbol")
        self.input_symbol.setText("BTC")
        form_layout.addWidget(self.input_symbol, 0, 0)

        self.input_contract_type = QLineEdit(self)
        self.input_contract_type.setPlaceholderText("Enter Contract Type")
        self.input_contract_type.setText("CRYPTO")
        self.input_contract_type.setStyleSheet("padding: 5px;")
        form_layout.addWidget(self.input_contract_type, 1, 0)

        self.input_exchange = QLineEdit(self)
        self.input_exchange.setPlaceholderText("Enter Exchange")
        self.input_exchange.setText("PAXOS")
        self.input_exchange.setStyleSheet("padding: 5px;")
        form_layout.addWidget(self.input_exchange, 1, 1)

        self.input_brick_size = QLineEdit(self)
        self.input_brick_size.setPlaceholderText("Enter Renko Brick Size")
        self.input_brick_size.setText("50")
        self.input_brick_size.setStyleSheet("padding: 5px;")
        form_layout.addWidget(self.input_brick_size, 0, 1)

        self.comboBoxtf = QComboBox(self)
        time_durations = [
            "1 sec", "5 secs", "15 secs", "30 secs",
            "1 min", "2 mins", "3 mins", "5 mins",
            "15 mins", "30 mins", "1 hour"
        ]
        self.comboBoxtf.addItems(time_durations)
        self.comboBoxtf.setPlaceholderText("Enter Timeframe")
        self.comboBoxtf.setCurrentText("5 mins")  # Default value
        form_layout.addWidget(self.comboBoxtf, 2, 0, 1, 0)

        self.button = QPushButton('Update Data', self)
        self.button.setStyleSheet("""
        QPushButton {
            background-color: brown;
            color: white;
            padding: 10px;
            font-weight: bold;
            border-radius: 5px;
        }
        QPushButton:hover {
            background-color: darkbrown;
        }
        """)
        self.button.clicked.connect(self.start_data_fetch) 
        form_layout.addWidget(self.button, 4, 0)

        # Animated Start/Stop Trading Button
        self.trading_button = QPushButton('Start Trading', self)
        self.trading_button.setStyleSheet("""
        QPushButton {
            background-color: gray;
            color: white;
            padding: 10px;
            font-weight: bold;
            border-radius: 5px;
        }
        QPushButton:hover {
            background-color: lightgray;
        }
        """)
        self.trading_button.clicked.connect(self.toggle_trading)
        form_layout.addWidget(self.trading_button, 4, 1)
        
        self.buy_radio_button = QRadioButton('Buy', self)
        self.buy_radio_button.setStyleSheet("""
        QRadioButton {
            color: blue;
            font-weight: bold;
            padding: 10px;
        }
        """)
        self.sell_radio_button = QRadioButton('Sell', self)
        self.sell_radio_button.setStyleSheet("""
        QRadioButton {
            color: red;
            font-weight: bold;
            padding: 10px;
        }
        """)
        self.all_radio_button = QRadioButton('All', self)
        self.all_radio_button.setStyleSheet("""
            QRadioButton{
                color: gold;
                font-weight: bold;
                padding: 10px;
            }
        """)
        
        self.radio_button_group = QButtonGroup(self)
        self.radio_button_group.addButton(self.buy_radio_button)
        self.radio_button_group.addButton(self.sell_radio_button)
        self.radio_button_group.addButton(self.all_radio_button)
        form_layout.addWidget(self.buy_radio_button, 3, 0)
        form_layout.addWidget(self.sell_radio_button, 3, 1)
        form_layout.addWidget(self.all_radio_button,  3, 2)
        
        self.all_radio_button.setChecked(True)

        self.input_quantity = QLineEdit(self)
        self.input_quantity.setPlaceholderText("Enter Quantity")
        self.input_quantity.setText("500")
        self.input_quantity.setStyleSheet("padding: 5px;")
        form_layout.addWidget(self.input_quantity, 5, 0)
        
        self.tws_client.input_quantity = self.input_quantity

        # self.comboBoxFieldOption = QComboBox(self)
        # self.comboBoxFieldOption.addItems(["Option", "Future"])
        # self.comboBoxFieldOption.setPlaceholderText("Select Field Option and Future")
        # self.comboBoxFieldOption.setCurrentIndex(-1)  # No default selection
        # form_layout.addWidget(self.comboBoxFieldOption, 5, 1)
        
        layout.addLayout(form_layout)
        self.setLayout(layout)
        
        self.tws_client.connect("127.0.0.1", port, index)
        time.sleep(0.5)
        threading.Thread(target=self.tws_client.run_loop, daemon=True).start()
        
        
    def toggle_trading(self):
        self.trading = not self.trading
        self.tws_client.trading = self.trading

        if self.trading:
            self.trading_button.setText("Stop Trading")
            self.animate_button(self.trading_button, "red")  # Ensure the button turns red
            self.disable_form_elements()
            self.tws_client.order_stack.clear()

            self.tws_client.buy_only = self.buy_radio_button.isChecked()
           
            self.tws_client.sell_only = self.sell_radio_button.isChecked()
     
            self.tws_client.all_buy_sell = self.all_radio_button.isChecked()
            
            # self.handle_initial_order()
        
        else:
            self.trading_button.setText("Start Trading")
            self.trading_button.setStyleSheet("""
            QPushButton {
                background-color: green;
                color: white;
                padding: 10px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: darkgreen;
            }
        """)
        self.enable_form_elements()
        self.button.setEnabled(True)
        
    def disable_form_elements(self):
        for widget in self.findChildren(QWidget):
            if widget != self.trading_button:
                widget.setEnabled(False)
                
    def enable_form_elements(self):
        for widget in self.findChildren(QWidget):
            if widget != self.trading_button:
                widget.setEnabled(True)

    def animate_button(self, button, color):
        start_color = "grey" if color == "green" else color
        end_color = color
        button.setStyleSheet(f"""
        QPushButton {{
            background-color: {end_color};
            color: white;
            padding: 10px;
            font-weight: bold;
            border-radius: 5px;
        }}
        QPushButton:hover {{
            background-color: dark{end_color};
        }}
        """)
        self.timer.setSingleShot(True)
        self.timer.start(500)
        
    def start_data_fetch(self):
        symbol = self.input_symbol.text().strip().upper()
        contract_type = self.input_contract_type.text().strip().upper()
        exchange = self.input_exchange.text().strip().upper()
        brick_size = self.input_brick_size.text().strip()
        time_frame = self.comboBoxtf.currentText()
        # field_option = self.comboBoxFieldOption.currentText()
        quantity = self.input_quantity.text().strip()
        
        # Ensure contract is initialized
        self.tws_client.contract = Contract()
        self.tws_client.contract.symbol = symbol
        self.tws_client.contract.secType = contract_type
        self.tws_client.contract.exchange = exchange
        self.tws_client.contract.currency = "USD"
        self.tws_client.brick_size = float(brick_size)

        # Check if contract is properly initialized
        if not self.tws_client.contract:
        
            self.tws_client.cancelHistoricalData(self.tws_client.active_req_ids)
            self.tws_client.active_req_ids.clear()  # clear the initial request.
        
        # # Cancel any request
        for active_req_id in self.tws_client.active_req_ids:
            self.tws_client.cancelHistoricalData(active_req_id)
        self.tws_client.active_req_ids.clear()  # clear the initial request.
        
        # Proceed with fetching historical data
        self.tws_client.reqHistoricalData(self.tws_client.reqid, self.tws_client.contract, "", time_frame_durations[time_frame], time_frame, "MIDPOINT", 0, 1, True, [])
        
        self.tws_client.active_req_ids.add(self.tws_client.reqid)
        self.tws_client.reqid += 1

        
# Main application window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Real-Time Stock Chart with TWS API")
        self.setGeometry(self.screen().geometry())
        self.centralWidget = QWidget()
        self.widget1 = StockChartWidget(self, 1)
        self.widget2 = StockChartWidget(self, 2)
        self.widget3 = StockChartWidget(self, 3)
        self.layout = QHBoxLayout()
        self.layout.addWidget(self.widget1)
        self.layout.addWidget(self.widget2)
        self.layout.addWidget(self.widget3)
        self.centralWidget.setLayout(self.layout)
        self.setCentralWidget(self.centralWidget)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    main = MainWindow()
    main.show()
    sys.exit(app.exec_())
