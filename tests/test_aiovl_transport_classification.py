"""Retry classification uses status only, never provider error bodies."""
import asyncio
import io
import unittest
import urllib.error
from unittest.mock import Mock, patch

from aiovl import Bot, CloudBotTransportError


class TransportClassificationTests(unittest.TestCase):
    def test_http_status_classification_and_closed_error_body(self):
        async def scenario():
            for status in (400, 401, 403, 404, 409, 429, 500, 502, 503, 504):
                body = io.BytesIO(b'secret-provider-details')
                error = urllib.error.HTTPError('https://example.invalid/private', status,
                                               'secret-provider-details', {}, body)
                opener = Mock()
                opener.open.side_effect = error
                with patch('aiovl.client.urllib.request.build_opener', return_value=opener):
                    with self.assertRaises(CloudBotTransportError) as caught:
                        await Bot('synthetic-token-0123456789').get_updates('bot_test_01')
                self.assertEqual(caught.exception.retryable, status in (429, 502, 503, 504))
                self.assertNotIn('secret', str(caught.exception))
                self.assertTrue(body.closed)
        asyncio.run(scenario())

    def test_injected_connection_failure_is_retryable_but_invalid_response_is_not(self):
        async def scenario():
            async def disconnected(*args):
                raise ConnectionError('secret details')
            async def malformed(*args):
                return {'unexpected': 'secret details'}
            async def programming_error(*args):
                raise ValueError('secret details')
            for transport, expected in ((disconnected, True), (malformed, False), (programming_error, False)):
                with self.assertRaises(CloudBotTransportError) as caught:
                    await Bot('synthetic-token-0123456789', transport=transport).get_updates('bot_test_01')
                self.assertEqual(caught.exception.retryable, expected)
                self.assertNotIn('secret', str(caught.exception))
        asyncio.run(scenario())
