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
    CONF_USERNAME, CONF_PASSWORD,
    CONF_URL, EVENT_HOMEASSISTANT_START,
    EVENT_HOMEASSISTANT_STOP, EVENT_STATE_CHANGED)
from homeassistant.helpers import state
import homeassistant.helpers.config_validation as cv

REQUIREMENTS = ['elasticsearch==5.4.0']

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'elasticsearch'
CONF_ESINDICE = 'indice'
CONF_ESTYPE = 'type'

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Optional(CONF_URL, default=['http://localhost:9200']):
            vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_ESINDICE, default='hass'): cv.string,
        vol.Optional(CONF_ESTYPE, default='event'): cv.string,
        vol.Optional(CONF_USERNAME): cv.string,
        vol.Optional(CONF_PASSWORD, default=''): cv.string,
    }),
}, extra=vol.ALLOW_EXTRA)


def setup(hass, config):
    """Setup the Elasticsearch feeder."""
    from elasticsearch import Elasticsearch
    conf = config[DOMAIN]
    es_args = {}
    if CONF_USERNAME in conf:
        es_args['http_auth'] = (conf[CONF_USERNAME], conf[CONF_PASSWORD])

    es_logger = logging.getLogger('elasticsearch')
    es_logger.propagate = True
    es_logger.setLevel(logging.INFO)
    try:
        es_client = Elasticsearch(conf.get(CONF_URL), **es_args)
    except Exception:
        _LOGGER.error('Not able to connect to Elasticsearch', exc_info=False)
        return False

    ElasticsearchFeeder(hass, es_client, conf.get(CONF_ESINDICE), conf.get(CONF_ESTYPE))
    return True


class ElasticsearchFeeder(threading.Thread):
    """Feed data to Elasticsearch."""

    def __init__(self, hass, es_client, es_indice, es_type):
        """Initialize the feeder."""
        super(ElasticsearchFeeder, self).__init__(daemon=True)

        self._hass = hass
        self._queue = queue.Queue()
        self._quit_object = object()
        self._we_started = False
        self._es_client = es_client
        self.es_indice = es_indice
        self.es_type = es_type

        hass.bus.listen_once(EVENT_HOMEASSISTANT_START, self.start_listen)
        hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, self.shutdown)
        hass.bus.listen(EVENT_STATE_CHANGED, self.event_listener)
        _LOGGER.debug('Elasticsearch feeding initialized')

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
        try:
            self._es_client.index(index=self.es_indice, doc_type=self.es_type, body=data)
        except Exception:
            _LOGGER.error('Not able to send to Elasticsearch')

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
