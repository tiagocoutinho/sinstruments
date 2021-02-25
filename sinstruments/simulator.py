# -*- coding: utf-8 -*-
#
# This file is part of the sinstruments project
#
# Copyright (c) 2018-present Tiago Coutinho
# Distributed under the GPLv3 license. See LICENSE for more info.

"""Instrument Simulator

To start the server you can do something like::

    $ python -m sinstruments -c my_simulator.yml
"""

from __future__ import print_function

import io
import os
import pty
import sys
import inspect
import logging
import weakref

import click
import gevent
from gevent.server import StreamServer, DatagramServer
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


class BaseProtocol:

    def __init__(self, device, channel, transport):
        self.device = device
        self.channel = channel
        self.transport = transport
        self.is_generator = inspect.isgeneratorfunction(self.device.handle_message)

    def handle(self):
        raise NotImplementedError


class MessageProtocol(BaseProtocol):

    def handle(self):
        for message in self.read_messages():
            self.handle_message(message)

    def handle_message(self, message):
        """
        Handle a single command.

        Arguments:
            message (str): message to be processed

        Returns:
            str: response to give to client or None if no response
        """
        if self.is_generator:
            for reply in self.device.handle_message(message):
                if reply is not None:
                    self.transport.send(self.channel, reply)
        else:
            reply = self.device.handle_message(message)
            if reply is not None:
                self.transport.send(self.channel, reply)

    def read_messages(self):
        raise NotImplementedError


class LineProtocol(MessageProtocol):

    @property
    def newline(self):
        return self.device.newline

    def read_messages(self):
        transport = self.transport
        nl = self.newline
        if nl == b'\n':
            for line in transport.ireadlines(self.channel):
                yield line
        else:
            # warning: in this mode read will block even if client
            # disconnects. Need to find a better way to handle this
            buff = b''
            while True:
                readout = transport.read1(self.channel)
                if not readout:
                    return
                buff += readout
                lines = buff.split(nl)
                buff, lines = lines[-1], lines[:-1]
                for line in lines:
                    if line:
                        yield line


class SimulatorServerMixin(object):
    """
    Mixin class for TCP/UDP/Serial servers to handle message based commands.
    Internal usage only
    """

    def __init__(self, name, handler, **kwargs):
        name = "{}[{}]".format(name, self.address)
        self.baudrate = kwargs.get('baudrate', None)
        self._log = logging.getLogger("{0}.{1}".format(_log.name, name))
        self._log.info("listening on %s (baud=%s)", self.address, self.baudrate)
        self.handler = handler
        self.props = kwargs

    def handle(self, channel):
        handler = self.handler(channel, self)
        try:
            handler.handle()
        except Exception as err:
            self._log.info('error handling requests: %r', err)

    def broadcast(self, msg):
        raise NotImplementedError

    def send(self, channel, data):
        raise NotImplementedError

    def read(self, channel, size=-1):
        data = channel.read(size)
        delay(len(data), baudrate=self.baudrate)
        return data

    def read1(self, channel, size=-1):
        data = channel.read1(size)
        delay(len(data), baudrate=self.baudrate)
        return data

    def readline(self, channel):
        data = channel.readline()
        delay(len(data), baudrate=self.baudrate)
        return data

    def ireadlines(self, channel):
        for line in channel:
            delay(len(line), baudrate=self.baudrate)
            yield line


class SerialServer(SimulatorServerMixin):
    """
    Serial line emulation server. It uses :func:`pty.opentpy` to open a
    pseudo-terminal simulating a serial line.
    """

    def __init__(self, name, handler, **kwargs):
        self.address = kwargs.pop("url", '')
        self.set_listener(kwargs.pop('listener', None))
        SimulatorServerMixin.__init__(self, name, handler, **kwargs)

    def stop(self):
        self.close()

    def close(self):
        if os.path.islink(self.address):
            os.remove(self.address)
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

        self.original_address = os.ttyname(self.slave)
        self.fileobj = FileObject(self.master, mode='rb')

        # Make a link to the randomly named pseudo-terminal with a known name.
        link_path, link_fname = os.path.split(self.address)
        try:
            os.remove(self.address)
        except Exception:
            pass
        if not os.path.exists(link_path):
            os.makedirs(link_path)
        os.symlink(self.original_address, self.address)
        _log.info(
            'Created symbolic link "%s" to simulator pseudo terminal %r',
            self.address, self.original_address
        )

    def serve_forever(self):
        self.handle(self.fileobj)

    def broadcast(self, msg):
        self.send(self.fileobj, msg)

    def send(self, channel, data):
        delay(len(data), baudrate=self.baudrate)
        os.write(channel.fileno(), data)


class TCPServer(StreamServer, SimulatorServerMixin):
    """
    TCP emulation server
    """

    def __init__(self, name, handler, **kwargs):
        self.connections = {}
        listener = kwargs.pop("url")
        if isinstance(listener, list):
            listener = tuple(listener)
        StreamServer.__init__(self, listener)
        SimulatorServerMixin.__init__(self, name, handler, **kwargs)

    def handle(self, sock, addr):
        info = self._log.info
        info("new connection from %s", addr)
        channel = sock.makefile('rwb', 0)
        # non buffered rwb are SocketIO objects (not instances of io.BufferedIOBase)
        # so they don't have read1.
        def read1(size=-1):
            size = io.DEFAULT_BUFFER_SIZE if size == -1 else size
            return channel.read(size)
        channel.read1 = read1
        self.connections[addr] = sock
        try:
            SimulatorServerMixin.handle(self, channel)
        finally:
            del channel
            del self.connections[addr]
            sock.close()
        info("client disconnected %s", addr)

    def broadcast(self, msg):
        for _, sock in self.connections.items():
            try:
                sock.sendall(msg)
            except Exception:
                self._log.exception("error in broadcast")

    def send(self, channel, data):
        channel.write(data)


class UDPServer(DatagramServer, SimulatorServerMixin):
    """
    UDP emulation server
    """

    def __init__(self, name, handler, **kwargs):
        listener = kwargs.pop("url")
        if isinstance(listener, list):
            listener = tuple(listener)
        DatagramServer.__init__(self, listener)
        SimulatorServerMixin.__init__(self, name, handler, **kwargs)

    def handle(self, data, addr):
        handler = self.handler(addr, self)
        try:
            handler.handle_message(data)
        except Exception as err:
            self._log.info('error handling requests: %r', err, exc_info=1)

    def broadcast(self, msg):
        pass

    def send(self, channel, data):
        sent, total = 0, len(data)
        while sent < total:
            sent += self.socket.sendto(data[sent:], channel)


class BaseDevice(object):
    """
    Base intrument class. Override to implement a simulator for a specific
    device
    """

    protocol = LineProtocol
    newline = b"\n"
    baudrate = None

    def __init__(self, name, server=None, **kwargs):
        self.name = name
        self.server = server
        self.newline = kwargs.pop('newline', self.newline)
        self._log = logging.getLogger("{0}.{1}".format(_log.name, name))
        self.transports = []
        self.props = kwargs

    def get_protocol(self, channel, transport):
        return self.protocol(self, channel, transport)

    def on_connection(self, transport, conn):
        pass

    def handle_message(self, message):
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

    def __init__(self, devices=(), backdoor=None, registry=None):
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

        self.registry = {} if registry is None else registry
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
        name = device_info["name"]
        # name should be unique
        assert name not in self.devices
        self._log.info("Creating device %s (%r)", name, klass_name)
        device_info["server"] = self
        device = create_device(device_info, self.registry)
        self.devices[name] = device
        return device

    def get_device_by_name(self, name):
        return self.devices[name]

    def start(self):
        tasks = []
        for device in self.devices.values():
            for transport in device.transports:
                tasks.append(gevent.spawn(transport.serve_forever))
        return tasks

    def stop(self):
        for device in self.devices.values():
            for transport in device.transports:
                transport.stop()

    def serve_forever(self):
        tasks = self.start()
        try:
            gevent.joinall(tasks)
        finally:
            self.stop()

    def __str__(self):
        return "{0}({1})".format(self.__class__.__name__, self.name)


def create_device(device_info, registry):
    device_info = dict(device_info)
    class_name = device_info.pop("class")
    module_name = device_info.pop("module", None)
    package_name = device_info.pop("package", None)
    name = device_info.pop("name")

    if package_name is None and module_name is None:
        klass = registry[class_name].load()
    else:
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
        ikwargs.setdefault('baudrate', device.baudrate)
        itype = ikwargs.pop("type", "tcp")
        if itype == "tcp":
            iklass = TCPServer
        elif itype == "udp":
            iklass = UDPServer
        elif itype == "serial":
            iklass = SerialServer
        transports.append(iklass(device.name, device.get_protocol, **ikwargs))
    device.transports = transports
    return device


def load_device_registry():
    """
    Return device classes for those devices which registered themselves with an
    entry point.
    """
    import pkg_resources
    return {
        ep.name: ep
        for ep in pkg_resources.iter_entry_points('sinstruments.device')
    }


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
    registry = load_device_registry()
    return Server(devices=devices, backdoor=backdoor, registry=registry)


@click.group(invoke_without_command=True, help=__doc__.split("\n")[1])
@click.pass_context
@click.option(
    "--log-level",
    type=click.Choice(["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"], case_sensitive=False),
    default="WARNING",
    help="log level (case insensitive)",
    show_default=True,
)
@click.option(
    "-c", "--config-file",
    type=click.Path(),
    help="configuration file",
    default="./sinstruments.yml"
)
def cli(ctx, log_level, config_file):
    fmt = "%(asctime)-15s %(levelname)-5s %(name)s: %(message)s"
    logging.basicConfig(format=fmt, level=log_level)

    if ctx.invoked_subcommand is None:
        config = parse_config_file(config_file)
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


@cli.command("ls", help="Lists available sinstruments plugins")
def ls():
    for name, plugin in load_device_registry().items():
        print(plugin.dist)


main = cli


if __name__ == "__main__":
    exit(main())
