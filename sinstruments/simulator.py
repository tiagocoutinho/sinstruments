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
from gevent.baseserver import BaseServer

_log = logging.getLogger("simulator")

__all__ = [
    "Server",
    "BaseDevice",
    "SimulatorServerMixin",
    "SerialServer",
    "TCPServer",
    "main",
]


class SimulatorServerMixin(object):
    """
    Mixin class for TCP/Serial servers to handle line based commands.
    Internal usage only
    """

    def __init__(self, device=None, newline=None, baudrate=None):
        self.device = device
        self.baudrate = baudrate
        self.newline = device.newline if newline is None else newline
        self.special_messages = set(device.special_messages)
        self.connections = {}
        name = "{}[{}]".format(device.name, self.address)
        self._log = logging.getLogger("{0}.{1}".format(_log.name, name))
        self._log.info(
            "listening on %s (newline=%r) (baudrate=%s)",
            self.address,
            self.newline,
            self.baudrate,
        )

    def handle(self, sock, addr):
        file_obj = sock.makefile(mode="rb")
        self.connections[addr] = file_obj, sock
        try:
            return self.__handle(sock, file_obj)
        finally:
            file_obj.close()
            del self.connections[addr]

    def __handle(self, sock, file_obj):
        """
        Handle new connection and requests

        Arguments:
            sock (gevent.socket.socket): new socket resulting from an accept
            addr tuple): address (tuple of host, port)
        """
        self.device.on_connection(self, sock)
        if self.newline == "\n" and not self.special_messages:
            for line in file_obj:
                self.handle_line(sock, line)
        else:
            # warning: in this mode read will block even if client
            # disconnects. Need to find a better way to handle this
            buff = ""
            finish = False
            while not finish:
                readout = file_obj.read(1)
                if not readout:
                    return
                buff += readout
                if buff in self.special_messages:
                    lines = (buff,)
                    buff = ""
                else:
                    lines = buff.split(self.newline)
                    buff, lines = lines[-1], lines[:-1]
                for line in lines:
                    if not line:
                        return
                    self.handle_line(sock, line)

    def handle_line(self, sock, line):
        """
        Handle a single command line. Simulates a delay if baudrate is defined
        in the configuration.

        Arguments:
            sock (gevent.socket.socket): new socket resulting from an accept
            addr (tuple): address (tuple of host, port)
            line (str): line to be processed

        Returns:
            str: response to give to client or None if no response
        """
        self.pause(len(line))
        response = self.device.handle_line(line)
        if response is not None:
            self.pause(len(response))
            sock.sendall(response)

        return response

    def pause(self, nb_bytes):
        """
        Simulate a delay simulating the transport of the given number of bytes,
        correspoding to the baudrate defined in the configuration

        Arguments:
            nb_bytes (int): number of bytes to transport
        """
        # simulate baudrate
        if not self.baudrate:
            return
        byterate = self.baudrate / 10.0
        sleep_time = nb_bytes / byterate
        gevent.sleep(sleep_time)

    def broadcast(self, msg):
        for _, (_, sock) in self.connections.items():
            try:
                sock.sendall(msg)
            except:
                self._log.exception("error in broadcast")


class SerialServer(BaseServer, SimulatorServerMixin):
    """
    Serial line emulation server. It uses :func:`pty.opentpy` to open a
    pseudo-terminal simulating a serial line.
    """

    def __init__(self, *args, **kwargs):
        device = kwargs.pop("device")
        self.link_name = kwargs.pop("url")
        e_kwargs = dict(
            baudrate=kwargs.pop("baudrate", None), newline=kwargs.pop("newline", None)
        )
        BaseServer.__init__(self, None, *args, **kwargs)
        SimulatorServerMixin.__init__(self, device, **e_kwargs)

    def __del__(self):
        try:
            print("Removing pseudo terminal link : %s" % self.link_name)
            os.remove(self.link_name)
        except:
            print("pseudo terminal link no more present ?")

    def terminate(self):
        try:
            print(
                "terminate of SerialServer : Removing pseudo terminal link : %s"
                % self.link_name
            )
            os.remove(self.link_name)
        except:
            print("pseudo terminal link no more present ?")

    def set_listener(self, listener):
        """
        Override of :meth:`~gevent.baseserver.BaseServer.set_listener` to
        initialize a pty and properly fill the address
        """
        if listener is None:
            self.master, self.slave = pty.openpty()
        else:
            self.master, self.slave = listener

        self.address = os.ttyname(self.slave)
        self.fileobj = FileObject(self.master, mode="rb")

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

    @property
    def socket(self):
        """
        Override of :meth:`~gevent.baseserver.BaseServer.socket` to return a
        socket object for the pseudo-terminal file object
        """
        return self.fileobj._sock

    def _do_read(self):
        # override _do_read to properly handle pty
        try:
            self.do_handle(self.socket, self.address)
        except:
            self.loop.handle_error(([self.address], self), *sys.exc_info())
            if self.delay >= 0:
                self.stop_accepting()
                self._timer = self.loop.timer(self.delay)
                self._timer.start(self._start_accepting_if_started)
                self.delay = min(self.max_delay, self.delay * 2)


class TCPServer(StreamServer, SimulatorServerMixin):
    """
    TCP emulation server
    """

    def __init__(self, *args, **kwargs):
        listener = kwargs.pop("url")
        if isinstance(listener, list):
            listener = tuple(listener)
        device = kwargs.pop("device")
        e_kwargs = dict(
            baudrate=kwargs.pop("baudrate", None), newline=kwargs.pop("newline", None)
        )
        StreamServer.__init__(self, listener, *args, **kwargs)
        SimulatorServerMixin.__init__(self, device, **e_kwargs)

    def handle(self, sock, addr):
        info = self._log.info
        info("new connection from %s", addr)
        SimulatorServerMixin.handle(self, sock, addr)
        info("client disconnected %s", addr)


class BaseDevice(object):
    """
    Base intrument class. Override to implement an Simulator for a specific
    device
    """

    DEFAULT_NEWLINE = "\n"

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

    def terminate(self):
        for device in self.devices:
            for tp in device.transports:
                tp.terminate()

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
        for device in self.devices:
            for interface in self.devices[device]:
                interface.start()

    def stop(self):
        for device in self.devices:
            for interface in self.devices[device]:
                interface.stop()

    def serve_forever(self):
        stop_events = []
        for device in self.devices:
            for interface in self.devices[device]:
                stop_events.append(interface._stop_event)
        self.start()
        try:
            gevent.joinall(stop_events)
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
    parsers = {
        '.yml': 'yaml',
        '.yaml': 'yaml',
        '.json': 'json',
        '.toml': 'toml',
    }
    ext = os.path.splitext(file_name)[-1]
    parser = __import__(parsers[ext])
    with open(file_name, 'r') as config_file:
        return parser.load(config_file)


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
            server.terminate()
            print("Server terminated... I'll be back.")
        except:
            print("No terminate function for server or error in terminating.")


if __name__ == "__main__":
    main()
