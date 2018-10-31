# -*- coding: utf-8 -*-
#
# This file is part of the instrument simulator project
#
# Copyright (c) 2018 Tiago Coutinho
# Distributed under the MIT. See LICENSE for more info.

"""Base simulator device"""

import logging

__all__ = ['BaseDevice']


class BaseDevice(object):
    """
    Base intrument class. Override to implement an Simulator for a specific
    device
    """

    DEFAULT_NEWLINE = b'\n'

    special_messages = set()

    def __init__(self, name, newline=None, **kwargs):
        self.name = name
        self.newline = self.DEFAULT_NEWLINE if newline is None else newline
        self._log = logging.getLogger(name)
        self.__transports = {}
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
        self.__transports = { transport: None for transport in transports }

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
