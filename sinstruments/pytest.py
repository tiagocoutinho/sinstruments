# -*- coding: utf-8 -*-
#
# This file is part of the sinstruments project
#
# Copyright (c) 2018-present Tiago Coutinho
# Distributed under the GPLv3 license. See LICENSE for more info.

"""
pytest server context helper

Example usage:

```python
import pytest

from sinstruments.pytest import server_context

cfg = {
    "devices": [{
        "name": "oscillo-1",
        "class": "Oscilloscope",
        "transports": [
            {"type": "tcp", "url": "localhost:0"}
        ]
    }]
}

# example 1: use as a context manager
def test_oscilloscope_id():
    with server_context(cfg) as server:
        # put here code to perform your tests that need to communicate with
        # the simulator. In this example an oscilloscope client
        addr = server.devices["oscillo-1"].transports[0].address
        oscillo = Oscilloscope(addr)
        assert oscillo.idn().startswith("ACME Inc,O-3000")


# example 2: use a predefined fixture (depends on an existing config fixture)

from sinstruments.pytest import server

@pytest.fixture
def config()
    yield cfg

def test_oscilloscope_voltage(server):
    addr = server.devices["oscillo-1"].transports[0].address
    oscillo = Oscilloscope(addr)
    assert 5 < oscillo.voltage() < 10


# example 3: define your own fixture

@pytest.fixture
def oscillo_server():
    with server_context(config) as server:
        server.oscillo1 = server.devices["oscillo-1"]
        server.oscillo1.addr = server.oscillo1.transports[0].address
        yield server


def test_oscilloscope_current(oscillo_server):
    oscillo = Oscilloscope(oscillo_server.oscillo1.addr)
    assert .05 < oscillo.current() < 0.01
```

"""

import socket
import threading

import pytest
import gevent.event

from .simulator import create_server_from_config


class server_context:

    def __init__(self, config):
        self.config = config
        self.thread = None

    def start(self):
        started_event = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(started_event,))
        self.thread.start()
        started_event.wait()

    def stop(self):
        if self.thread is None:
            return
        self.watcher.start(self.stop_event.set)
        self.watcher.send()
        self.thread.join()
        self.thread = None

    def _run(self, started_event):
        self.server = create_server_from_config(self.config)
        self.server.stop_thread_safe = self.stop
        self.watcher = gevent.get_hub().loop.async_()
        self.stop_event = gevent.event.Event()
        # we start each transport manually to make sure all sockets are open
        # before we notify that we have started. Otherwise tests might get
        # random ConnectionRefused errors
        for device in self.server.devices.values():
            for transport in device.transports:
                transport.start()
        server_task = gevent.spawn(self.server.serve_forever)
        started_event.set()
        self.stop_event.wait()
        server_task.kill()
        self.watcher.close()

    def __enter__(self):
        self.start()
        return self.server

    def __exit__(self, exc_type, exc_value, tb):
        self.stop()


@pytest.fixture
def server(config):
    with server_context(config) as server:
        yield server
