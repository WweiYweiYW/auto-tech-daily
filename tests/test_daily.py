import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import daily

NOW = datetime(2026, 11, 18, 5, 0, tzinfo=timezone.utc)
CONFIG = json.loads((daily.ROOT / 'config.json').read_text(encoding='utf-8'))


def rss(title='汽车线控制动技术', date='Wed, 18 Nov 2026 00:00:00 GMT', guid='one', link='https://example.com/one'):
    return f'<rss><channel><item><guid>{guid}</guid><title>{title}</title><description><![CDATA[<p>汽车悬架技术原文</p>]]></description><link>{link}</link><pubDate>{date}</pubDate></item></channel></rss>'.encode()


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.config = copy.deepcopy(CONFIG)
        self.config['feeds'] = [CONFIG['feeds'][0]]
        self.sender = MagicMock()
        self.feishu_sender = MagicMock()
        self.env = {'SMTP_USERNAME': 'private@example.com', 'SMTP_PASSWORD': 'never-print'}

    def run_daily(self, **kwargs):
        options = dict(config=self.config, state_path=self.path, now=NOW, env={}, fetch=lambda _: rss(), sender=self.sender, feishu_sender=self.feishu_sender)
        options.update(kwargs)
        return daily.run(**options)

    def test_rss_and_atom(self):
        item = daily.parse_feed(rss(), 'test')[0]
        self.assertEqual(item['summary'], '汽车悬架技术原文')
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>a</id><title>EV battery</title><summary>new battery</summary><link href="https://example.com/a"/><updated>2026-11-18T00:00:00Z</updated></entry></feed>'
        self.assertEqual(daily.parse_feed(atom, 'test')[0]['link'], 'https://example.com/a')
        self.assertEqual(daily.parse_feed(rss(link='javascript:alert(1)'), 'test'), [])
        with self.assertRaises(ValueError):
            daily.parse_feed(b'<html><body>Not a feed</body></html>', 'test')

    def test_keywords(self):
        cases = [('汽车底盘悬架技术', True, '底盘技术'), ('手机系统控制更新', True, None), ('手机电池快充', True, None), ('汽车快充技术', True, '汽车新技术'), ('汽车悬架优惠促销', True, None), ('New suspension damper', False, '底盘技术'), ('EV battery thermal management', False, '汽车新技术'), ('New control system', False, None), ('carpet battery', True, None), ('Battery discount', False, None)]
        for title, context, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(daily.classify({'title': title, 'summary': ''}, {'require_auto_context': context}, self.config), expected)

    def test_dedup_across_runs_and_guid_change(self):
        self.run_daily()
        self.run_daily(fetch=lambda _: rss(guid='changed'))
        self.assertEqual(len(daily.load_state(self.path)['items']), 1)
        self.sender.assert_not_called()

    def test_first_window_missing_date_and_later_discovery(self):
        for date in ('Mon, 16 Nov 2026 00:00:00 GMT', ''):
            self.run_daily(fetch=lambda _, d=date: rss(date=d))
            self.assertFalse(daily.load_state(self.path)['items'])
            self.path.unlink()
        self.run_daily(fetch=lambda _: b'<rss><channel/></rss>')
        self.run_daily(fetch=lambda _: rss(date=''))
        self.assertEqual(len(daily.load_state(self.path)['items']), 1)

    def test_one_source_failure_and_all_failure(self):
        self.config['feeds'] = CONFIG['feeds']
        def fetch(url):
            if 'ithome' in url:
                raise TimeoutError('private data')
            return rss()
        code, messages = self.run_daily(fetch=fetch)
        self.assertEqual(code, 0)
        self.assertEqual(len(daily.load_state(self.path)['items']), 1)
        self.assertNotIn('private data', '\n'.join(messages))
        code, _ = self.run_daily(fetch=MagicMock(side_effect=TimeoutError), env=self.env)
        self.assertEqual(code, 1)
        self.sender.assert_not_called()

    def test_timezone_and_catchup(self):
        state = daily.new_state()
        self.assertFalse(daily.due(state, NOW.replace(hour=4, minute=29)))
        self.assertTrue(daily.due(state, NOW.replace(hour=4, minute=30)))
        self.assertTrue(daily.due(state, NOW.replace(hour=12)))
        state['last_sent_dates']['email'] = '2026-11-18'
        self.assertFalse(daily.due(state, NOW))
        self.assertTrue(daily.due(state, NOW + timedelta(days=1)))
        self.assertEqual(daily.parse_date('2026-11-18T12:30:00+08:00'), NOW.replace(hour=4, minute=30))
        self.assertFalse(daily.due(state, NOW.replace(hour=17)))  # Next Shanghai date, before 12:30.

    def test_success_and_same_day_no_resend(self):
        code, messages = self.run_daily(env=self.env)
        self.assertEqual(code, 0)
        self.assertTrue(any('邮件已发 1 篇' in line for line in messages))
        self.assertEqual(next(iter(daily.load_state(self.path)['items'].values()))['status'], 'sent')
        self.run_daily(env=self.env, fetch=lambda _: rss(guid='two', link='https://example.com/two'))
        self.assertEqual(self.sender.call_count, 1)

    def test_failure_preserves_pending_and_retry(self):
        self.sender.side_effect = RuntimeError('private@example.com never-print')
        code, messages = self.run_daily(env=self.env)
        self.assertEqual(code, 1)
        self.assertNotIn('private@example.com', '\n'.join(messages))
        self.assertIsNone(daily.load_state(self.path)['last_sent_dates']['email'])
        self.assertEqual(next(iter(daily.load_state(self.path)['items'].values()))['status'], 'pending')
        self.sender.side_effect = None
        self.run_daily(env=self.env)
        self.assertEqual(self.sender.call_count, 2)

    def test_no_content_missing_credentials_and_force(self):
        self.run_daily(env=self.env, fetch=lambda _: b'<rss><channel/></rss>')
        self.sender.assert_not_called()
        code, messages = self.run_daily()
        self.assertEqual(code, 0)
        self.assertTrue(any('未配置' in line for line in messages))
        # Articles collected while a channel is disabled are deliberately skipped.
        self.run_daily(env=self.env, now=NOW.replace(hour=0, minute=0), force_send=True,
                       fetch=lambda _: rss(guid='two', link='https://example.com/two'))
        self.sender.assert_called_once()

    def test_dry_run_no_mutation_no_send(self):
        self.run_daily(dry_run=True, force_send=True, env=self.env)
        self.assertFalse(self.path.exists())
        self.run_daily()
        before = self.path.read_bytes()
        code, messages = self.run_daily(dry_run=True, force_send=True, env=self.env)
        self.assertEqual(code, 0)
        self.assertEqual(before, self.path.read_bytes())
        self.sender.assert_not_called()
        self.assertNotIn('private@example.com', '\n'.join(messages))

    def test_retention_pending_limit_and_html_escape(self):
        self.run_daily(env=self.env, now=NOW.replace(hour=0))
        self.config['max_pending'] = 1
        self.run_daily(env=self.env, now=NOW.replace(hour=0), fetch=lambda _: rss(guid='two', link='https://example.com/two'))
        items = list(daily.load_state(self.path)['items'].values())
        self.assertEqual(sum(i['status'] == 'pending' for i in items), 1)
        self.assertEqual(sum(i['status'] == 'discarded' for i in items), 1)
        item = items[0] | {'title': '<img onerror="x">', 'summary': '<script>x</script>'}
        markup = daily.render([item], '2026-11-18')[1]
        self.assertNotIn('<script>', markup)
        self.assertIn('&lt;img', markup)
        self.run_daily(now=NOW + timedelta(days=91), fetch=lambda _: b'<rss><channel/></rss>')
        self.assertFalse(daily.load_state(self.path)['items'])

    @patch('daily.smtplib.SMTP_SSL')
    def test_smtp_ssl_mock_only(self, smtp):
        connection = smtp.return_value.__enter__.return_value
        connection.send_message.return_value = {}
        item = daily.parse_feed(rss(), 'test')[0] | {'category': '底盘技术'}
        daily.send_mail([item], '2026-11-18', self.env)
        self.assertEqual(smtp.call_args.args, ('smtp.qq.com', 465))
        message = connection.send_message.call_args.args[0]
        self.assertEqual(message['To'], self.env['SMTP_USERNAME'])
        connection.send_message.return_value = {'rejected': (550, b'no')}
        with self.assertRaises(RuntimeError):
            daily.send_mail([item], '2026-11-18', self.env)

    @patch('daily.time.sleep')
    @patch('daily.urlopen', side_effect=TimeoutError)
    def test_bounded_network_retries(self, opener, sleep):
        with self.assertRaises(TimeoutError):
            daily.fetch_feed('https://example.com/rss')
        self.assertEqual(opener.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == '__main__':
    unittest.main()
