# -*- coding: utf-8 -*-
#
# This file is part of the instrument simulator project
#
# Copyright (c) 2018 Tiago Coutinho
# Distributed under the MIT. See LICENSE for more info.

"""Instrument Simulator

To start the server you can do something like::

    $ python -m sinstruments -c my_simulator.yml
"""

from __future__ import print_function

import os
import pty
import sys
import logging
import weakref

import gevent
from gevent.server import StreamServer
from gevent.fileobject import FileObject


_log = logging.getLogger("simulator")

__all__ = [
    "Server",
    "BaseDevice",
    "SimulatorServerMixin",
    "SerialServer",
    "TCPServer",
    "main",
]


def delay(nb_bytes, baudrate=None):
    """
    Simulate a delay simulating the transport of the given number of bytes,
    correspoding to the baudrate defined in the configuration

    Arguments:
        nb_bytes (int): number of bytes to transport
    """
    # simulate baudrate
    if not baudrate:
        return
    byterate = baudrate / 10.0
    sleep_time = nb_bytes / byterate
    gevent.sleep(sleep_time)


class BaseHandler:

    def __init__(self, fobj, transport):
        self.fobj = fobj
        self.transport = transport

    @property
    def device(self):
        return self.transport.device

    @property
    def baudrate(self):
        return self.transport.baudrate


class LineHandler(BaseHandler):

    @property
    def newline(self):
        return self.transport.newline

    @property
    def special_messages(self):
        return self.transport.special_messages

    def readlines(self):
        nl = self.newline
        if nl == b'\n' and not self.special_messages:
            for line in self.fobj:
                yield line
        else:
            # warning: in this mode read will block even if client
            # disconnects. Need to find a better way to handle this
            buff = b''
            while True:
                readout = self.fobj.read(1)
                if not readout:
                    return
                buff += readout
                if buff in self.special_messages:
                    lines = (buff,)
                    buff = b''
                else:
                    lines = buff.split(nl)
                    buff, lines = lines[-1], lines[:-1]
                for line in lines:
                    if line:
                        yield line

    def handle(self):
        for line in self.readlines():
            self.handle_line(line)

    def handle_line(self, line):
        """
        Handle a single command line. Simulates a delay if baudrate is defined
        in the configuration.

        Arguments:
            line (str): line to be processed

        Returns:
            str: response to give to client or None if no response
        """
        delay(len(line), self.baudrate)
        response = self.transport.device.handle_line(line)
        if response is not None:
            delay(len(response), self.baudrate)
            self.transport.send(self.fobj, response)


class SimulatorServerMixin(object):
    """
    Mixin class for TCP/Serial servers to handle line based commands.
    Internal usage only
    """

    def __init__(self, device=None, newline=None, baudrate=None, handler=LineHandler):
        self.device = device
        self.baudrate = baudrate
        self.newline = device.newline if newline is None else newline
        self.special_messages = set(device.special_messages)
        name = "{}[{}]".format(device.name, self.address)
        self._log = logging.getLogger("{0}.{1}".format(_log.name, name))
        self._log.info(
            "listening on %s (newline=%r) (baudrate=%s)",
            self.address,
            self.newline,
            self.baudrate,
        )
        self.handler = handler

    def handle(self, fobj):
        h = self.handler(fobj, self)
        try:
            h.handle()
        except Exception as err:
            self._log.info('error handling requests: %r', err)

    def broadcast(self, msg):
        raise NotImplementedError

    def send(self, fobj, data):
        raise NotImplementedError


class SerialServer(SimulatorServerMixin):
    """
    Serial line emulation server. It uses :func:`pty.opentpy` to open a
    pseudo-terminal simulating a serial line.
    """

    def __init__(self, *args, **kwargs):
        device = kwargs.pop("device")
        self.link_name = kwargs.pop("url")
        self.set_listener(kwargs.pop('listener', None))
        SimulatorServerMixin.__init__(self, device, **kwargs)

    def stop(self):
        self.close()

    def close(self):
        if os.path.islink(self.link_name):
            os.remove(self.link_name)
        if self.master:
            os.close(self.master)
            self.master = None
        if self.slave:
            os.close(self.slave)
            self.slave = None

    def set_listener(self, listener):
        if listener is None:
            self.master, self.slave = pty.openpty()
        else:
            self.master, self.slave = listener

        self.address = os.ttyname(self.slave)
        self.fileobj = FileObject(self.master, mode='rb')

        # Make a link to the randomly named pseudo-terminal with a known name.
        link_path, link_fname = os.path.split(self.link_name)
        try:
            os.remove(self.link_name)
        except:
            pass
        if not os.path.exists(link_path):
            os.makedirs(link_path)
        os.symlink(self.address, self.link_name)
        _log.info('Created symbolic link "%s" to simulator pseudo ' \
                  'terminal "%s" ', self.link_name, self.address)

    def serve_forever(self):
        self.handle(self.fileobj)

    def broadcast(self, msg):
        self.fileobj.write(msg)

    def send(self, fobj, data):
        os.write(fobj.fileno(), data)


class TCPServer(StreamServer, SimulatorServerMixin):
    """
    TCP emulation server
    """

    def __init__(self, *args, **kwargs):
        self.connections = {}
        listener = kwargs.pop("url")
        if isinstance(listener, list):
            listener = tuple(listener)
        device = kwargs.pop("device")
        e_kwargs = dict(
            baudrate=kwargs.pop("baudrate", None),
            newline=kwargs.pop("newline", None)
        )
        StreamServer.__init__(self, listener, *args, **kwargs)
        SimulatorServerMixin.__init__(self, device, **e_kwargs)

    def handle(self, sock, addr):
        info = self._log.info
        info("new connection from %s", addr)
        fobj = sock.makefile('rwb', 0)
        self.connections[addr] = sock
        self.device.on_connection(self, sock)
        try:
            SimulatorServerMixin.handle(self, fobj)
        finally:
            del self.connections[addr]
            sock.close()
        info("client disconnected %s", addr)

    def broadcast(self, msg):
        for _, sock in self.connections.items():
            try:
                sock.sendall(msg)
            except Exception:
                self._log.exception("error in broadcast")

    def send(self, fobj, data):
        fobj.write(data)


class BaseDevice(object):
    """
    Base intrument class. Override to implement an Simulator for a specific
    device
    """

    DEFAULT_NEWLINE = b"\n"

    special_messages = set()

    def __init__(self, name, newline=None, **kwargs):
        self.name = name
        self.newline = self.DEFAULT_NEWLINE if newline is None else newline
        self._log = logging.getLogger("{0}.{1}".format(_log.name, name))
        self.__transports = weakref.WeakKeyDictionary()
        if kwargs:
            self._log.warning(
                "constructor keyword args ignored: %s", ", ".join(kwargs.keys())
            )

    @property
    def transports(self):
        """the list of registered transports"""
        return self.__transports.keys()

    @transports.setter
    def transports(self, transports):
        self.__transports.clear()
        for transport in transports:
            self.__transports[transport] = None

    def on_connection(self, transport, conn):
        pass

    def handle_line(self, line):
        """
        To be implemented by the device.

        Raises: NotImplementedError
        """
        raise NotImplementedError

    def broadcast(self, msg):
        """
        broadcast the given message to all the transports

        Arguments:
            msg (str): message to be broadcasted
        """
        for transport in self.transports:
            transport.broadcast(msg)


class Server(object):
    """
    The instrument simulator server

    Handles a set of devices
    """

    def __init__(self, devices=(), backdoor=None):
        self._log = _log
        self._log.info("Bootstraping server")
        if backdoor:
            from gevent.backdoor import BackdoorServer

            banner = (
                "Welcome to Simulator server console.\n"
                "You can access me through the "
                "'server()' function. Have fun!"
            )
            self.backdoor = BackdoorServer(
                backdoor, banner=banner, locals=dict(server=weakref.ref(self))
            )
            self.backdoor.start()
            self._log.info("Backdoor opened at %r", backdoor)
        else:
            self._log.info("no backdoor declared")

        self.devices = {}
        for device in devices:
            try:
                self.create_device(device)
            except Exception as error:
                dname = device.get("name", device.get("class", "unknown"))
                self._log.error(
                    "error creating device %s (will not be available): %s", dname, error
                )
                self._log.debug("details: %s", error, exc_info=1)

    def create_device(self, device_info):
        klass_name = device_info.get("class")
        name = device_info.get("name", klass_name)
        self._log.info("Creating device %s (%r)", name, klass_name)
        device, transports = create_device(device_info)
        self.devices[device] = transports
        return device, transports

    def get_device_by_name(self, name):
        for device in self.devices:
            if device.name == name:
                return device

    def start(self):
        tasks = []
        for device in self.devices:
            for interface in self.devices[device]:
                tasks.append(gevent.spawn(interface.serve_forever))
        return tasks

    def stop(self):
        for device in self.devices:
            for transport in self.devices[device]:
                transport.stop()

    def serve_forever(self):
        tasks = self.start()
        try:
            gevent.joinall(tasks)
        finally:
            self.stop()

    def __str__(self):
        return "{0}({1})".format(self.__class__.__name__, self.name)


def create_device(device_info):
    device_info = dict(device_info)
    class_name = device_info.pop("class")
    module_name = device_info.pop("module", class_name.lower())
    package_name = device_info.pop("package", None)
    name = device_info.pop("name", class_name)

    if package_name is None:
        package_name = "sinstruments.simulators." + module_name

    __import__(package_name)
    package = sys.modules[package_name]
    klass = getattr(package, class_name)
    device = klass(name, **device_info)

    transports_info = device_info.pop("transports", ())
    transports = []
    for interface_info in transports_info:
        ikwargs = dict(interface_info)
        itype = ikwargs.pop("type", "tcp")
        if itype == "tcp":
            iklass = TCPServer
        elif itype == "serial":
            iklass = SerialServer
        ikwargs["device"] = device
        transports.append(iklass(**ikwargs))
    device.transports = transports
    return device, transports


def parse_config_file(file_name):
    ext = os.path.splitext(file_name)[-1]
    if ext.endswith('toml'):
        from toml import load
    elif ext.endswith('yml') or ext.endswith('.yaml'):
        import yaml
        def load(fobj):
            return yaml.load(fobj, Loader=yaml.Loader)
    elif ext.endswith('json'):
        from json import load
    else:
        raise NotImplementedError
    with open(file_name)as fobj:
        return load(fobj)


def create_server_from_config(config):
    backdoor, devices = config.get("backdoor", None), config.get("devices", ())
    return Server(devices=devices, backdoor=backdoor)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument(
        "--log-level",
        default="WARNING",
        help="log level",
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
    )
    parser.add_argument(
        "-c", "--config-file", default='./sinstruments.yml',
        help="configuration file",
    )
    args = parser.parse_args()

    fmt = "%(asctime)-15s %(levelname)-5s %(name)s: %(message)s"
    level = getattr(logging, args.log_level.upper())
    logging.basicConfig(format=fmt, level=level)
    config = parse_config_file(args.config_file)
    server = create_server_from_config(config)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCtrl-C Pressed. Bailing out...")
        try:
            server.stop()
        except Exception:
            logging.exception("Error while stopping.")
            return 1


if __name__ == "__main__":
    exit(main())
