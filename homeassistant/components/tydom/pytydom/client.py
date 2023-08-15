import http.client
import logging
import aiohttp
import ssl
import time
import threading
from .websocket import TydomWebSocket
from .utils import build_digest_headers, generate_random_key


class TydomConfigException(BaseException):
    """host and port must be specified!"""


class DeviceInfo:
    pass


class Client:
    """Class to handle talking to the tydom server."""

    _session = None
    _tydomsocket = None
    _host = None
    _port = None
    _exit = False
    _auth_mode = False
    _state_callbacks = []
    ready = False

    @property
    def host(self):
        """Return the tydom host."""
        return self._host

    @property
    def port(self):
        """Return the tydom port."""
        return self._port

    @property
    def auth_mode(self):
        """Return the tydom port."""
        if self._auth_mode:
            return "secured"
        return "open"

    def __init__(self, host, port):
        self._host = host
        self._port = port
        self._ssl_context = ssl._create_unverified_context()
        self._authorize_header = ""

        if not (host and port):
            raise TydomConfigException

        logging.debug("Finished TydomAPI start")

    def start(self, blocking=True):
        self._server_setup()

        # block untill we're ready
        if blocking:
            while not self.ready and not self._exit:
                time.sleep(0.05)

        # start socket watcher
        thread_id = threading.Thread(target=self._socket_watcher)
        thread_id.daemon = True
        thread_id.start()
        logging.debug("Finished TydomAPI start")

    def __exit__(self, type, value, exc_tb):
        """Stop socket on exit."""
        self.stop()

    def __enter__(self):
        """Just return self on entry."""
        return self

    def stop(self):
        """Stop socket."""
        self._exit = True
        if self._tydomsocket:
            self._tydomsocket.stop()

    async def authorize(self, login, password=""):
        """
        Check if Authorization is enabled
        """
        httpHeaders = {
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            # "Host": f"{self._host}:{self._port}",
            "Accept": "*/*",
            "Sec-WebSocket-Key": generate_random_key().decode("utf-8"),
            "Sec-WebSocket-Version": "13",
        }
        url_path = f"/mediation/client?mac={login}&appli=1"
        url = f"https://{self._host}{url_path}"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=httpHeaders, ssl=self._ssl_context
            ) as resp:
                assert resp.status == 101
                if "WWW-Authenticate" in resp.headers:
                    nonce = resp.headers["WWW-Authenticate"].split(",", 3)
                    self._authorize_header = build_digest_headers(
                        login, password, url, nonce
                    )
                    self._auth_mode = True
                    print("auth enabled")
                else:
                    print("auth disabled")
                    self._auth_mode = False
        print("end authorize")
        return self._auth_mode

    def _server_setup(self):
        """Open the tydom socket connection to the tydom server on the network."""
        logging.debug("Connecting to Tydom server %s:%s", self._host, self._port)
        ws_address = (
            f"wss://{self._host}:{self._port}/mediation/client?mac={self._port}&appli=1"
        )
        self._tydomsocket = TydomWebSocket(
            ws_address,
            {"Authorization": self._authorize_header} if self._auth_mode else {},
            self._ssl_context,
        )

        self._tydomsocket.register_connected_callback(self._socket_connected)

        self._tydomsocket.start()

    def _socket_connected(self):
        """Successfully connected the websocket."""
        logging.debug("Connection with Tydom websockets (re)created.")
        self.ready = False

    def _on_state_change(self, msg):
        """Process messages we receive from the tydom websocket into a more usable format."""
        if not msg or not isinstance(msg, dict):
            return
        print(msg)

    def _request(self, command, data=None):
        """Send command and wait for result."""
        logging.debug("_request: command: %s", command)
        if not self._tydomsocket:
            retries = 20
            while (not self.ready or not self._tydomsocket) and retries:
                retries -= 1
                time.sleep(0.2)
            if not self.ready or not self._tydomsocket:
                logging.warning("socket is not yet ready")
                if not self._tydomsocket:
                    return None
        logging.debug("_request: sending")
        request_id = self._tydomsocket.send_request(command, data)
        result = None
        retries = 50
        while retries:
            result = self._tydomsocket.results.get(request_id)
            logging.debug(
                "request: command: %s, retry: %d, success: %s",
                command,
                retries,
                result is not None,
            )
            if result:
                break
            retries -= 1

            time.sleep(0.05)
        try:
            del self._tydomsocket.results[request_id]
        except KeyError:
            pass
        return result

    def _socket_watcher(self):
        """Monitor the connection state of the socket and reconnect if needed."""
        while not self._exit:
            if self._tydomsocket and self._tydomsocket.failed_state:
                logging.warning("Socket connection lost! Will try to reconnect in 20s")
                count = 0
                while not self._exit and count < 21:
                    count += 1
                    time.sleep(1)
                if not self._exit:
                    self._server_setup()
            time.sleep(2)

    def register_state_callback(self, callback, event_filter=None):
        """
        Register a callback to be informed about changes.
        params:
            callback: method to be called when state changes occur, it will be passed an event param as string and a list of changed objects
                      callback will be called with params:
                      - event: string with name of the event ("zones_changed", "zones_seek_changed", "outputs_changed")
                      - a list with the zone or output id's that changed
            event_filter: only callback if the event is in this list
        """
        if not event_filter:
            event_filter = []
        elif not isinstance(event_filter, list):
            event_filter = [event_filter]
        self._state_callbacks.append((callback, event_filter))

    def get_ping(self):
        self._tydomsocket.send_request("GET /ping")

    def get_info(self):
        self._tydomsocket.send_request("GET /info")

    def get_configs_file(self):
        self._tydomsocket.send_request("GET /configs/file")

    def get_configs_gateway_geoloc(self):
        self._tydomsocket.send_request("GET /configs/gateway/geoloc")

    def put_configs_gateway_api_mode(self):
        self._tydomsocket.send_request("PUT /configs/gateway/api_mode")

    def put_configs_gateway_password(self, new_password, old_password):
        self._tydomsocket.send_request(
            "PUT /configs/gateway/password",
            body=f'{{"new":"{new_password}","old":"{old_password}"}}',
        )

    def post_refresh_all(self):
        self._tydomsocket.send_request("POST /refresh/all")

    def get_areas_data(self):
        self._tydomsocket.send_request("GET /areas/data")

    def get_areas_cmeta(self):
        self._tydomsocket.send_request("GET /areas/cmeta")

    def get_areas_meta(self):
        self._tydomsocket.send_request("GET /areas/meta")

    def get_devices_cmeta(self):
        self._tydomsocket.send_request("GET /devices/cmeta")

    def get_devices_meta(self):
        self._tydomsocket.send_request("GET /devices/meta")

    def get_devices_data(self):
        self._tydomsocket.send_request("GET /devices/data")

    def get_device_data(self, device_id):
        self._tydomsocket.send_request(
            f"GET /devices/{device_id}/endpoints/{device_id}/data"
        )

    def put_device_data(self, device_id, name, value):
        _body = f'[{{"name":"{name}","value":"{value}"}}]'
        self._tydomsocket.send_request(
            f"PUT /devices/{device_id}/endpoints/{device_id}/data", body=_body
        )

    def get_scenarii(self):
        self._tydomsocket.send_request("GET /scenarios/file")

    def put_scenario(self, scenario_id):
        _body = ""  # TODO: howto !
        self._tydomsocket.send_request(f"GET /scenarios/{scenario_id}", body=_body)

    def get_moments(self):
        self._tydomsocket.send_request("GET /moments/file")
