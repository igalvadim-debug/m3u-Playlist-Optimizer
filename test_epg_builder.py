"""Regression checks for EPG connection, redirect and decompression guards."""
import gzip
import http.client
import io
import socket
import unittest
import urllib.request
from email.message import Message
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.response import addinfourl

from modules import epg_builder as epg


class SafeConnectionTests(unittest.TestCase):
    def test_connections_use_guard_without_changing_global_socket_factory(self):
        original = socket.create_connection
        ordinary = http.client.HTTPConnection('example.com')
        for connection_type in (epg._SafeHTTPConnection, epg._SafeHTTPSConnection):
            with self.subTest(connection=connection_type.__name__):
                sock = Mock()

                def connect(address, timeout, source_address):
                    self.assertIs(socket.create_connection, original)
                    self.assertIs(ordinary._create_connection, original)
                    self.assertEqual(address, ('example.com', 8443))
                    self.assertEqual(timeout, 7)
                    self.assertEqual(source_address, ('0.0.0.0', 0))
                    return sock

                with patch.object(epg, '_safe_socket_connect', side_effect=connect) as guard:
                    connection = connection_type('example.com', 8443, timeout=7,
                                                 source_address=('0.0.0.0', 0))
                    if isinstance(connection, http.client.HTTPSConnection):
                        connection._context = Mock()
                        connection._context.wrap_socket.return_value = sock
                    connection.connect()
                    guard.assert_called_once()
                self.assertIs(socket.create_connection, original)

    def test_connections_reject_private_dns_answers_before_opening_socket(self):
        for connection_type in (epg._SafeHTTPConnection, epg._SafeHTTPSConnection):
            with self.subTest(connection=connection_type.__name__):
                connection = connection_type('example.com', timeout=7)
                answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))]
                with patch.object(socket, 'getaddrinfo', return_value=answers), \
                        patch.object(socket, 'socket') as make_socket:
                    with self.assertRaisesRegex(ValueError, 'запрещённый адрес'):
                        connection.connect()
                    make_socket.assert_not_called()

    def test_guard_uses_validated_address_and_socket_factory_arguments(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        with patch.object(socket, 'getaddrinfo', return_value=answers) as resolve, \
                patch.object(socket, 'socket') as make_socket:
            sock = epg._safe_socket_connect(('example.com', 443), 7, ('0.0.0.0', 0))
            resolve.assert_called_once_with('example.com', 443, 0, socket.SOCK_STREAM)
            sock.settimeout.assert_called_once_with(7)
            sock.bind.assert_called_once_with(('0.0.0.0', 0))
            sock.connect.assert_called_once_with(('8.8.8.8', 443))
            self.assertIs(sock, make_socket.return_value)


class SafeRedirectTests(unittest.TestCase):
    def test_opener_checks_every_redirect_target(self):
        self.assertTrue(any(isinstance(handler, epg._SafeRedirectHandler)
                            for handler in epg._opener.handlers))

        class RedirectSource(urllib.request.BaseHandler):
            handler_order = 100

            def __init__(self, target):
                self.target = target
                self.seen = []

            def http_open(self, req):
                self.seen.append(req.full_url)
                headers = Message()
                if len(self.seen) == 1:
                    headers['Location'] = '/next.xml'
                else:
                    headers['Location'] = self.target
                response = addinfourl(io.BytesIO(b''), headers, req.full_url, 302)
                response.msg = 'Found'
                return response

        for target in ('ftp://8.8.8.8/epg.xml', 'http://127.0.0.1/epg.xml',
                       'https://[::1]/epg.xml', 'file:///etc/passwd',
                       'http://private.example/epg.xml'):
            with self.subTest(target=target):
                source = RedirectSource(target)
                opener = urllib.request.build_opener(source, epg._SafeRedirectHandler())
                private_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 80))]
                with patch.object(epg, 'validate_epg_url', wraps=epg.validate_epg_url) as validate, \
                        patch.object(socket, 'getaddrinfo', return_value=private_dns):
                    with self.assertRaises((ValueError, HTTPError)):
                        opener.open('http://8.8.8.8/start.xml')
                self.assertEqual(source.seen,
                                 ['http://8.8.8.8/start.xml', 'http://8.8.8.8/next.xml'])
                self.assertEqual(validate.call_args_list[0].args, ('http://8.8.8.8/next.xml',))
                if not target.startswith('file:'):
                    self.assertEqual(validate.call_args_list[-1].args, (target,))

    def test_public_https_redirect_is_allowed(self):
        handler = epg._SafeRedirectHandler()
        request = urllib.request.Request('http://8.8.8.8/start.xml')
        redirected = handler.redirect_request(request, None, 302, 'Found', {},
                                               'https://8.8.4.4/epg.xml')
        self.assertEqual(redirected.full_url, 'https://8.8.4.4/epg.xml')


class StreamLimitTests(unittest.TestCase):
    def test_gzip_and_plain_streams_are_limited(self):
        data = b'<tv>' + b' ' * 4096 + b'</tv>'
        for body in (data, gzip.compress(data)):
            with self.subTest(gzip=body != data):
                with patch.object(epg, 'MAX_DOWNLOAD_BYTES', 256), \
                        patch.object(epg._opener, 'open', return_value=io.BytesIO(body)):
                    stream = epg.EpgBuilder._open_stream('https://8.8.8.8/epg')
                    self.assertEqual(stream.read(100), data[:100])
                    self.assertEqual(stream.read(), data[100:256])
                    self.assertEqual(stream.read(), b'')

    def test_truncated_gzip_xml_is_reported_by_build(self):
        body = gzip.compress(b'<tv>' + b' ' * 4096 + b'</tv>')
        with patch.object(epg, 'MAX_DOWNLOAD_BYTES', 256), \
                patch.object(epg._opener, 'open', return_value=io.BytesIO(body)):
            builder = epg.EpgBuilder()
            builder.build([], 'https://8.8.8.8/epg')
        self.assertEqual(len(builder.stats['errors']), 1)
        self.assertIn('no element found', builder.stats['errors'][0])

    def test_small_gzip_xml_still_parses(self):
        body = gzip.compress(b'<tv><channel id="news.ru"><display-name>News</display-name></channel></tv>')
        with patch.object(epg._opener, 'open', return_value=io.BytesIO(body)):
            builder = epg.EpgBuilder()
            stream = builder._open_stream('https://8.8.8.8/epg')
            names = {}
            builder._filter_into(stream, {'news'}, names, io.BytesIO())
        self.assertEqual(names, {'news': 'news.ru'})


if __name__ == '__main__':
    unittest.main()
