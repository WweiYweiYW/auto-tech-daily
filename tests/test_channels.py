import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.request import Request

import daily
from test_daily import CONFIG, NOW, rss

WEBHOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/test-token'


def many_feed(count):
    entries = [rss(guid=str(i), link=f'https://example.com/{i}').split(b'<channel>')[1].split(b'</channel>')[0] for i in range(count)]
    return b'<rss><channel>' + b''.join(entries) + b'</channel></rss>'


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.config = copy.deepcopy(CONFIG)
        self.config['feeds'] = [CONFIG['feeds'][0]]
        self.email = MagicMock()
        self.feishu = MagicMock()
        self.env = {'SMTP_USERNAME': 'private@example.com', 'SMTP_PASSWORD': 'private-password',
                    'FEISHU_WEBHOOK': WEBHOOK, 'FEISHU_SIGN_SECRET': 'private-sign-secret'}

    def run_daily(self, **kwargs):
        options = dict(config=self.config, state_path=self.path, now=NOW, env=self.env,
                       fetch=lambda _: rss(), sender=self.email, feishu_sender=self.feishu)
        options.update(kwargs)
        return daily.run(**options)

    def items(self):
        return list(daily.load_state(self.path)['items'].values())

    def legacy(self, status='sent'):
        item = daily.parse_feed(rss(), CONFIG['feeds'][0]['name'])[0]
        item.update(category='底盘技术', seen_at=NOW.isoformat(), status=status)
        if status == 'sent':
            item['sent_at'] = NOW.isoformat()
        return {'version': 1, 'initialized': True, 'last_sent_date': '2026-11-18', 'items': {item['id']: item}}

    def test_legacy_sent_migration_never_replays_even_force(self):
        daily.save_state(self.path, self.legacy())
        self.run_daily(force_send=True)
        self.email.assert_not_called()
        self.feishu.assert_not_called()
        state = daily.load_state(self.path)
        self.assertEqual(state['version'], 2)
        self.assertEqual(state['last_sent_dates'], {'email': '2026-11-18', 'feishu': None})
        self.assertEqual(self.items()[0]['delivery'], {'email': 'sent', 'feishu': 'sent'})
        self.assertEqual(self.items()[0]['sent_at'], {'email': NOW.isoformat()})

    def test_legacy_pending_only_preserves_email(self):
        daily.save_state(self.path, self.legacy('pending'))
        self.run_daily(force_send=True)
        self.email.assert_called_once()
        self.feishu.assert_not_called()
        self.assertEqual(self.items()[0]['delivery'], {'email': 'sent', 'feishu': 'skipped'})

    def test_both_channels_success(self):
        self.assertEqual(self.run_daily()[0], 0)
        self.email.assert_called_once()
        self.feishu.assert_called_once()
        self.assertEqual(self.items()[0]['delivery'], {'email': 'sent', 'feishu': 'sent'})
        self.assertEqual(daily.load_state(self.path)['last_sent_dates'], dict.fromkeys(daily.CHANNELS, '2026-11-18'))

    def test_each_failure_retains_other_success_without_resend(self):
        for failing_name in ('email', 'feishu'):
            with self.subTest(channel=failing_name):
                if self.path.exists():
                    self.path.unlink()
                self.email.reset_mock()
                self.feishu.reset_mock()
                failed = self.email if failing_name == 'email' else self.feishu
                other = self.feishu if failing_name == 'email' else self.email
                failed.side_effect = RuntimeError('secret URL ' + WEBHOOK)
                code, messages = self.run_daily()
                self.assertEqual(code, 1)
                self.assertNotIn(WEBHOOK, '\n'.join(messages))
                self.assertEqual(self.items()[0]['delivery'][failing_name], 'pending')
                self.assertEqual(self.items()[0]['delivery']['feishu' if failing_name == 'email' else 'email'], 'sent')
                failed.side_effect = None
                self.run_daily()
                self.assertEqual(failed.call_count, 2)
                self.assertEqual(other.call_count, 1)
                self.run_daily(force_send=True)
                self.assertEqual(failed.call_count, 2)
                self.assertEqual(other.call_count, 1)

    def test_independent_dates_and_late_channel_only_new_articles(self):
        self.run_daily(env={k: v for k, v in self.env.items() if k.startswith('SMTP')})
        self.assertEqual(self.items()[0]['delivery']['feishu'], 'skipped')
        self.run_daily(fetch=lambda _: rss(guid='two', link='https://example.com/two'))
        self.email.assert_called_once()
        self.feishu.assert_called_once()
        self.assertEqual([i['link'] for i in self.feishu.call_args.args[0]], ['https://example.com/two'])
        self.run_daily(force_send=True)
        self.assertEqual(self.email.call_count, 2)
        self.assertEqual(self.feishu.call_count, 1)

    def test_feishu_only_and_partial_configuration(self):
        env = {'FEISHU_WEBHOOK': WEBHOOK, 'SMTP_USERNAME': 'private@example.com'}
        code, messages = self.run_daily(env=env)
        self.assertEqual(code, 0)
        self.assertTrue(any('邮件凭据配置不完整' in m for m in messages))
        self.email.assert_not_called()
        self.feishu.assert_called_once()
        self.assertEqual(self.items()[0]['delivery'], {'email': 'skipped', 'feishu': 'sent'})
        code, messages = self.run_daily(env={'FEISHU_SIGN_SECRET': 'secret'})
        self.assertEqual(code, 0)
        self.assertTrue(any('飞书凭据配置不完整' in m for m in messages))

    def test_disabled_collection_not_backfilled(self):
        self.run_daily(env={})
        self.run_daily(force_send=True)
        self.email.assert_not_called()
        self.feishu.assert_not_called()
        self.assertEqual(self.items()[0]['delivery'], dict.fromkeys(daily.CHANNELS, 'skipped'))

    def test_feishu_batch_remainder_next_day_and_force(self):
        self.run_daily(fetch=lambda _: many_feed(25))
        self.assertEqual(len(self.email.call_args.args[0]), 25)
        first = {i['id'] for i in self.feishu.call_args.args[0]}
        self.assertEqual(len(first), 12)
        self.run_daily(fetch=lambda _: many_feed(25))
        self.assertEqual(self.feishu.call_count, 1)
        self.run_daily(now=NOW + timedelta(days=1), fetch=lambda _: many_feed(25))
        second = {i['id'] for i in self.feishu.call_args.args[0]}
        self.assertEqual(len(second), 12)
        self.assertFalse(first & second)
        self.run_daily(force_send=True, now=NOW + timedelta(days=1), fetch=lambda _: many_feed(25))
        self.assertEqual(len(self.feishu.call_args.args[0]), 1)
        self.assertTrue(all(i['delivery']['feishu'] == 'sent' for i in self.items()))
        self.assertEqual(self.email.call_count, 1)

    def test_dryrun_legacy_bytes_unchanged_both_senders_unused(self):
        daily.save_state(self.path, self.legacy())
        before = self.path.read_bytes()
        self.run_daily(dry_run=True, force_send=True, fetch=lambda _: many_feed(3))
        self.assertEqual(self.path.read_bytes(), before)
        self.email.assert_not_called()
        self.feishu.assert_not_called()


class FeishuTransportTests(unittest.TestCase):
    def setUp(self):
        self.env = {'FEISHU_WEBHOOK': WEBHOOK, 'FEISHU_SIGN_SECRET': 'test-secret'}
        self.item = daily.parse_feed(rss(), 'test')[0] | {'category': '底盘技术'}

    def test_known_signature_vector(self):
        self.assertEqual(daily.feishu_signature(1590000000, 'test-secret'), 'Weq+FVmAOX22zIY3sPXB95wQs/607cDtklQeiIb1JkI=')
        payload = json.loads(daily.feishu_payload([self.item], '2026-11-18', self.env, 1590000000))
        self.assertEqual(payload['timestamp'], '1590000000')
        self.assertEqual(payload['sign'], 'Weq+FVmAOX22zIY3sPXB95wQs/607cDtklQeiIb1JkI=')
        self.assertNotIn('sign', json.loads(daily.feishu_payload([self.item], '2026-11-18', {}, 1)))

    def test_utf8_size_and_long_content_no_article_lost(self):
        items = [self.item | {'id': str(i), 'title': f'{i}标题' + '🚗"\\' * 10000,
                             'source': '来源' * 1000, 'summary': '原文🚗"\\' * 10000,
                             'link': f'https://example.com/{i}/' + '长' * 10000} for i in range(12)]
        batch = daily.feishu_batch(items, '2026-11-18', self.env)
        raw = daily.feishu_payload(batch, '2026-11-18', self.env, 1590000000)
        self.assertLess(len(raw), 20000)
        text = json.loads(raw)['content']['text']
        self.assertIn('汽车技术日报', text)
        self.assertEqual(len(batch), 12)
        self.assertEqual(text.count('完整记录请查看仓库'), 12)
        for i in range(12):
            self.assertIn(f'{i}标题', text)

    @patch('daily.build_opener')
    def test_safe_url_and_no_redirect(self, builder):
        invalid = ['http://open.feishu.cn/open-apis/bot/v2/hook/a', WEBHOOK + '?x=1', WEBHOOK + '#x',
                   WEBHOOK.replace('open.feishu.cn', 'open.feishu.cn.evil.com'),
                   WEBHOOK.replace('open.feishu.cn', 'user@open.feishu.cn'),
                   WEBHOOK.replace('open.feishu.cn', 'open.feishu.cn:443'), WEBHOOK + '/extra',
                   WEBHOOK + '\n', WEBHOOK.replace('test-token', '%2fsecret')]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                daily.send_feishu([self.item], '2026-11-18', {'FEISHU_WEBHOOK': url})
        builder.assert_not_called()
        with self.assertRaises(ValueError):
            daily.NoRedirect().redirect_request(Request(WEBHOOK), MagicMock(), 302, 'redirect', MagicMock(), 'https://evil.com')

    @patch('daily.build_opener')
    @patch('daily.time.time', return_value=1590000000)
    def test_http_and_json_success(self, clock, builder):
        response = builder.return_value.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b'{"code":0}'
        daily.send_feishu([self.item], '2026-11-18', self.env)
        request = builder.return_value.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertLess(len(request.data), 20000)
        self.assertIn('汽车技术日报', json.loads(request.data)['content']['text'])
        self.assertEqual(builder.return_value.open.call_args.kwargs['timeout'], 20)
        self.assertIsInstance(builder.call_args.args[0], daily.NoRedirect)

    @patch('daily.build_opener')
    def test_http_business_json_failure(self, builder):
        response = builder.return_value.open.return_value.__enter__.return_value
        for status, body in [(500, b'{"code":0}'), (302, b'{"code":0}'),
                             (200, b'{"code":19024,"msg":"SECRET URL"}'), (200, b'{}'),
                             (200, b'{"code":false}'), (200, b'{"code":"0"}'),
                             (200, b'not JSON'), (200, b'[]'), (200, b'x' * 65537)]:
            with self.subTest(status=status, body=body[:40]):
                response.status, response.read.return_value = status, body
                with self.assertRaises((RuntimeError, ValueError)) as error:
                    daily.send_feishu([self.item], '2026-11-18', self.env)
                self.assertNotIn('SECRET URL', str(error.exception))

    @patch('daily.build_opener')
    def test_oversized_batch_rejected_before_network(self, builder):
        with self.assertRaises(ValueError):
            daily.send_feishu([self.item] * 13, '2026-11-18', self.env)
        builder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
