import threading
import logging
import websocket
import json
from .utils import generate_request_id, parseData

try:
    import thread
except ImportError:
    import _thread as thread

class TydomWebSocket(
  threading.Thread
):  # pylint: disable=too-many-instance-attributes
  """Class to handle the tydom websocket connection."""

  @property
  def results(self):
    """Return the result of the previous request."""
    return self._results

  def register_connected_callback(self, callback):
    """To be called on connection."""
    self._connected_callback = callback

  def run(self):
    """Start the socket thread."""
    self._socket.run_forever(ping_interval=10, sslopt={"context": self._ssl_context})
    if not self._exit:
      logging.warning("Session unexpectedly disconnected!")
      self._exit = True
      self.failed_state = True
    else:
      logging.debug("socket connection closed")

  def stop(self):
    """Stop the socket thread."""
    self._exit = True
    self._socket.close()

  def __init__(self, host, headers, ssl_context):
    """Create the websocket connection to the roon server."""

    self._socket = None
    self._results = {}
    self._subkey = 0
    self._exit = False
    self._subscriptions = {}
    self.connected = False
    self.failed_state = False
    self._ssl_context = ssl_context

    self._connected_callback = lambda: None

    self._socket = websocket.WebSocketApp(
        host,
        on_message=self.on_message,
        on_error=self.on_error,
        on_open=self.on_open,
        on_close=self.on_close,
        header=headers,
    )
    threading.Thread.__init__(self)
    self.daemon = True

  # pylint: disable=too-many-branches
  def on_message(self, w_socket, message=None):
    """Handle message callback."""
    if not message:
      message = w_socket  # compatability fix because of change in websocket-client v0.49
    try:
      message = message.decode("utf-8")
      lines = message.split("\r\n")
      header = lines[0]
      body = ""

      request_id = None
      line_with_request_id = [
        line for line in lines if line.startswith("Transac-Id")
      ]
      if line_with_request_id:
        request_id = int(line_with_request_id[0].split("Transac-Id: ")[1])
      
      body = "".join(message.split("\r\n\r\n")[1:])
      
      body = parseData(body)
      if body and "{" in body:
        body = json.loads(body)
        print(body)
      else:
        print(message)
      
      # handle message
      if request_id in self._subscriptions:
        # this is callback for one of our subscriptions
        self._subscriptions[request_id]["callback"](body)
      else:
        # this is just a result for one of our requests
        self._results[request_id] = body
    except websocket.WebSocketConnectionClosedException:
      # This can happen while closing a connection - so ignore
      pass
    
    except Exception:  # pylint: disable=broad-except
      logging.exception("Error while parsing message '%s'", message)

  # pylint: disable=no-self-use
  def on_error(self, w_socket, error=None):
    """Handle error callback."""
    if not error:
      error = w_socket  # compatability fix because of change in websocket-client v0.49
    logging.info("on_error %s", error)

  # pylint: disable=unused-argument
  def on_close(self, w_socket, close_status_code, close_msg):
    """Handle closing the session."""
    logging.debug("session closed (%s) %s", close_msg, close_status_code)
    self.connected = False

  # pylint: disable=unused-argument
  def on_open(self, w_socket=None):
    """Handle opening the session."""
    logging.debug("Opened Websocket connection to the server...")
    self.connected = True
    thread.start_new_thread(self._connected_callback, ())

  def send_request(
    self, command, body=None, content_type="application/json; charset=UTF-8"
  ):
    """Send request to the tydom sever."""
    if not self.connected:
      logging.error("Connection is not (yet) ready!")
      return False
    request_id = generate_request_id()
    self._results[request_id] = None
    content_length = len(body) if body is not None else 0
    msg = (
      f"{command} HTTP/1.1\r\n"
      f"Content-Length: {content_length}\r\n"
      f"Content-Type: {content_type}\r\n"
      f"Transac-Id: {request_id}\r\n"
      "User-Agent: TydomClient/0.1\r\n\r\n"
    )
    if body is not None:
      body = json.dumps(body)
      msg += body
    print(msg)
    msg = bytes(msg, "utf-8")
    self._socket.send(msg, 0x2)
    return request_id
