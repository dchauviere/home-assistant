"""
Component that sends data to Elasticsearch.

"""
import logging
import queue
import threading
import datetime
import json
import requests

import voluptuous as vol

from homeassistant.const import (
    CONF_URL, EVENT_HOMEASSISTANT_START,
    EVENT_HOMEASSISTANT_STOP, EVENT_STATE_CHANGED)
from homeassistant.helpers import state
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DEFAULT_URL = 'http://localhost:9200/ha/states'
DOMAIN = 'elasticsearch'

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Optional(CONF_URL, default=DEFAULT_URL): cv.string,
    }),
}, extra=vol.ALLOW_EXTRA)


def setup(hass, config):
    """Setup the Elasticsearch feeder."""
    conf = config[DOMAIN]
    url = conf.get(CONF_URL)

    response = requests.post(url, timeout=10)
    # if response.status_code in (200, 201, 404):
    #     _LOGGER.debug('Connection to Elasticsearch possible')
    # else:
    #     _LOGGER.error('Not able to connect to Elasticsearch')
    #     return False

    ElasticsearchFeeder(hass, url)
    return True


class ElasticsearchFeeder(threading.Thread):
    """Feed data to Graphite."""

    def __init__(self, hass, url):
        """Initialize the feeder."""
        super(ElasticsearchFeeder, self).__init__(daemon=True)
        self._hass = hass
        self._url = url
        self._queue = queue.Queue()
        self._quit_object = object()
        self._we_started = False

        hass.bus.listen_once(EVENT_HOMEASSISTANT_START, self.start_listen)
        hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, self.shutdown)
        hass.bus.listen(EVENT_STATE_CHANGED, self.event_listener)
        _LOGGER.debug('Elasticsearch feeding to %s initialized', self._url)

    def start_listen(self, event):
        """Start event-processing thread."""
        _LOGGER.debug('Event processing thread started')
        self._we_started = True
        self.start()

    def shutdown(self, event):
        """Signal shutdown of processing event."""
        _LOGGER.debug('Event processing signaled exit')
        self._queue.put(self._quit_object)

    def event_listener(self, event):
        """Queue an event for processing."""
        if self.is_alive() or not self._we_started:
            _LOGGER.debug('Received event')
            self._queue.put(event)
        else:
            _LOGGER.error('Elasticsearch feeder thread has died, not '
                          'queuing event!')

    def _send_to_elasticsearch(self, data):
        """Send data to Elasticsearch."""
        response = requests.post(self._url, data=data, timeout=10)
        if response.status_code not in (200, 201):
            _LOGGER.error('Not able to send to Elasticsearch')

    def _report_attributes(self, entity_id, new_state):
        """Report the attributes."""
        now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f%z')
        things = dict(new_state.attributes)
        try:
            things['state'] = state.state_as_number(new_state)
        except ValueError:
            pass

        data = json.dumps({ '@timestamp': now,
                            'entity_id': entity_id,
                            'attributes': things })

        _LOGGER.debug('Sending to Elasticsearch: %s', data)
        self._send_to_elasticsearch(data)

    def run(self):
        """Run the process to export the data."""
        while True:
            event = self._queue.get()
            if event == self._quit_object:
                _LOGGER.debug('Event processing thread stopped')
                self._queue.task_done()
                return
            elif (event.event_type == EVENT_STATE_CHANGED and
                  event.data.get('new_state')):
                _LOGGER.debug('Processing STATE_CHANGED event for %s',
                              event.data['entity_id'])
                try:
                    self._report_attributes(event.data['entity_id'],
                                            event.data['new_state'])
                # pylint: disable=broad-except
                except Exception:
                    # Catch this so we can avoid the thread dying and
                    # make it visible.
                    _LOGGER.exception('Failed to process STATE_CHANGED event')
            else:
                _LOGGER.warning('Processing unexpected event type %s',
                                event.event_type)

            self._queue.task_done()
