import copy
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import daily
import web_digest
from deploy.check_config import valid_environment
from deploy import check_config
from test_daily import CONFIG, NOW, rss
from test_channels import WEBHOOK, many_feed


class ExportTests(unittest.TestCase):
    def test_article_allowlist_dates_sort_and_delivery_independence(self):
        state = daily.new_state()
        original = daily.parse_feed(rss(), 'source')[0]
        for index, status in enumerate(('sent', 'skipped', 'pending', 'discarded')):
            item = original | {'id': str(index), 'category': '底盘技术', 'status': status,
                               'seen_at': NOW.isoformat(), 'delivery': {'email': status},
                               'secret': 'not-exported', 'sent_at': {'email': NOW.isoformat()}}
            item['published'] = '2026-11-18T17:00:00+00:00' if index == 1 else None
            state['items'][str(index)] = item
        state['items']['bad-link'] = state['items']['0'] | {'link': 'https://user:password@example.com'}
        before = copy.deepcopy(state)
        payload = web_digest.export_payload(state, NOW)
        self.assertEqual(state, before)
        self.assertEqual(set(payload), {'updated_at', 'timezone', 'articles'})
        self.assertEqual(payload['timezone'], 'Asia/Shanghai')
        self.assertTrue(payload['updated_at'].endswith('+08:00'))
        self.assertEqual([a['id'] for a in payload['articles']], ['1', '2', '0'])
        self.assertEqual(payload['articles'][0]['date'], '2026-11-19')
        self.assertEqual(payload['articles'][1]['date'], '2026-11-18')
        self.assertEqual(set(payload['articles'][0]), {'id','title','summary','source','link','category','published','seen_at','date'})
        self.assertNotIn('not-exported', json.dumps(payload))

    def test_public_url_validation(self):
        for value in ('javascript:alert(1)', 'https://u:p@example.com', 'https://@example.com',
                      'https://example.com\n', 'https://example.com\\evil', 'https://example.com:bad',
                      '//example.com', 'https://', 'https://example.com/' + 'a' * 4096):
            with self.subTest(value=value[:60]):
                self.assertFalse(web_digest.public_url(value))
        self.assertTrue(web_digest.public_url('http://203.0.113.1/'))
        self.assertTrue(web_digest.public_url('https://example.com/daily?a=1&b=2'))

    def test_entry_url_only_accepts_nginx_root_without_parameters(self):
        for url in ('https://example.com', 'http://203.0.113.1/', 'https://example.com/index.html'):
            self.assertTrue(web_digest.valid_entry_url(url))
        for url in ('https://example.com/daily/', 'https://example.com/?a=1',
                    'https://example.com/#x', 'https://example.com/?', 'https://example.com/#',
                    'https://u:p@example.com/', 'javascript:alert(1)'):
            self.assertFalse(web_digest.valid_entry_url(url))

    def test_atomic_publish_and_failure_preserves_previous_page(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'site'
            renderer = MagicMock(return_value='<!doctype html><p>new</p>')
            with patch.dict(sys.modules, {'web_ui': SimpleNamespace(render_page=renderer)}):
                web_digest.publish(daily.new_state(), NOW, directory)
                target = directory / 'index.html'
                self.assertIn('new', target.read_text(encoding='utf-8'))
                before = target.read_bytes()
                with patch('web_digest.os.replace', side_effect=OSError('disk full')):
                    with self.assertRaises(OSError):
                        web_digest.publish(daily.new_state(), NOW, directory)
                self.assertEqual(target.read_bytes(), before)
                self.assertEqual([p.name for p in directory.iterdir()], ['index.html'])
                renderer.assert_called_with(web_digest.export_payload(daily.new_state(), NOW))

    def test_real_renderer_accepts_export_and_escapes_article_text(self):
        state = daily.new_state()
        item = daily.parse_feed(rss(), 'source')[0] | {
            'title': '<script>alert("x")</script>', 'category': '底盘技术',
            'status': 'sent', 'seen_at': NOW.isoformat(), 'private': 'NEVER_EXPORT_THIS'}
        state['items'][item['id']] = item
        with tempfile.TemporaryDirectory() as folder:
            web_digest.publish(state, NOW, folder)
            markup = (Path(folder) / 'index.html').read_text(encoding='utf-8')
        self.assertIn('&lt;script&gt;', markup)
        self.assertNotIn('<script>alert(', markup)
        self.assertNotIn('NEVER_EXPORT_THIS', markup)


class ProductionConfigTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.path = self.root / 'state.json'
        self.site = self.root / 'site'
        self.site.mkdir()
        (self.site / 'index.html').write_text('<p>ready</p>', encoding='utf-8')
        daily.save_state(self.path, daily.new_state())
        self.env = {'DAILY_STATE_PATH':str(self.path), 'WEB_OUTPUT_DIR':str(self.site), 'SEND_TIME':'18:00',
                    'WEB_BASE_URL':'https://example.com/',
                    'SMTP_USERNAME':'test@example.com', 'SMTP_PASSWORD':'secret'}
        for name, value in [('STATE_PATH', self.path), ('SITE_PATH', self.site)]:
            patcher = patch.object(check_config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Windows lacks POSIX permission bits. Model the deployment permissions,
        # while exercising real file reads, schemas, absence and symlink checks.
        self.modes = {self.path:0o600, self.site:0o755, self.site/'index.html':0o644}
        patcher = patch.object(check_config, 'mode_bits', side_effect=lambda path: self.modes.get(path, 0o755))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_service_requires_entry_and_selected_channel_credentials(self):
        env = self.env
        self.assertTrue(valid_environment(env))
        self.assertFalse(valid_environment(env | {'WEB_BASE_URL':''}))
        self.assertFalse(valid_environment(env | {'DAILY_STATE_PATH':'data/state.json'}))
        self.assertFalse(valid_environment(env | {'WEB_REMINDER_CHANNEL':'feishu'}))
        self.assertTrue(valid_environment(env | {'WEB_REMINDER_CHANNEL':'feishu', 'FEISHU_WEBHOOK':WEBHOOK}))

    def test_production_requires_explicit_18_hour_send_time(self):
        for value in (None, '', '12:30', '18:0', '24:00'):
            env = self.env.copy()
            if value is None:
                env.pop('SEND_TIME')
            else:
                env['SEND_TIME'] = value
            self.assertFalse(valid_environment(env))
        self.assertTrue(valid_environment(self.env))

    def test_missing_or_invalid_state_never_bootstraps(self):
        self.path.unlink()
        self.assertFalse(valid_environment(self.env))
        self.assertFalse(self.path.exists())
        for state in ({}, {'version':3}, daily.new_state() | {'initialized':'yes'},
                      daily.new_state() | {'items':{'bad':{}}}):
            self.path.write_text(json.dumps(state), encoding='utf-8')
            self.assertFalse(valid_environment(self.env))
        self.path.write_text('{broken', encoding='utf-8')
        self.assertFalse(valid_environment(self.env))

    def test_legacy_state_validated_in_memory_without_rewriting(self):
        item = daily.parse_feed(rss(), 'test')[0] | {'category':'底盘技术', 'seen_at':NOW.isoformat(), 'status':'sent'}
        legacy = {'version':1, 'initialized':True, 'last_sent_date':'2026-11-18', 'items':{item['id']:item}}
        self.path.write_text(json.dumps(legacy), encoding='utf-8')
        before = self.path.read_bytes()
        self.assertTrue(valid_environment(self.env))
        self.assertEqual(self.path.read_bytes(), before)

    def test_permissions_missing_index_and_read_write_access(self):
        for path, mode in ((self.path,0o644), (self.path,0o400), (self.site,0o700),
                           (self.site/'index.html',0o600), (self.site.parent,0o700)):
            with self.subTest(path=path, mode=mode):
                previous = self.modes.get(path)
                self.modes[path] = mode
                self.assertFalse(valid_environment(self.env))
                if previous is None:
                    self.modes.pop(path)
                else:
                    self.modes[path] = previous
        with patch('deploy.check_config.os.access', return_value=False):
            self.assertFalse(valid_environment(self.env))
        (self.site/'index.html').unlink()
        self.assertFalse(valid_environment(self.env))

    def test_symlink_paths_are_rejected_without_following_them(self):
        # Mock is_symlink for platforms where creating symlinks needs admin rights.
        original = Path.is_symlink
        for target in (self.path, self.site, self.site/'index.html', self.site.parent):
            with self.subTest(target=target), patch.object(Path, 'is_symlink',
                    lambda path: path == target or original(path)):
                self.assertFalse(valid_environment(self.env))


class WebRunTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.path = self.root / 'state.json'
        self.site = self.root / 'site'
        self.config = copy.deepcopy(CONFIG)
        self.config['feeds'] = CONFIG['feeds'][:1]
        self.env = {'WEB_OUTPUT_DIR': str(self.site), 'WEB_BASE_URL': 'https://example.com/',
                    'SMTP_USERNAME': 'test@example.com', 'SMTP_PASSWORD': 'secret',
                    'FEISHU_WEBHOOK': WEBHOOK, 'FEISHU_SIGN_SECRET': 'secret'}
        self.email, self.feishu = MagicMock(), MagicMock()
        self.renderer = MagicMock(return_value='<!doctype html><p>published</p>')
        modules = patch.dict(sys.modules, {'web_ui': SimpleNamespace(render_page=self.renderer)})
        modules.start()
        self.addCleanup(modules.stop)

    def run_daily(self, **kwargs):
        options = dict(config=self.config, state_path=self.path, now=NOW, env=self.env,
                       fetch=lambda _: rss(), sender=self.email, feishu_sender=self.feishu)
        options.update(kwargs)
        return daily.run(**options)

    def test_default_email_single_channel_publication_precedes_send_and_dedup(self):
        self.email.side_effect = lambda *_: self.assertTrue((self.site / 'index.html').is_file())
        self.assertEqual(self.run_daily()[0], 0)
        self.email.assert_called_once()
        self.feishu.assert_not_called()
        state = daily.load_state(self.path)
        self.assertEqual(next(iter(state['items'].values()))['delivery'], {'email':'sent','feishu':'skipped'})
        self.run_daily()
        self.email.assert_called_once()
        self.run_daily(fetch=lambda _: rss(guid='new', link='https://example.com/new'))
        self.email.assert_called_once()
        self.assertEqual(len(self.renderer.call_args.args[0]['articles']), 2)
        self.run_daily(now=NOW + timedelta(days=1))
        self.assertEqual(self.email.call_count, 2)

    def test_server_send_time_boundary_and_daily_dedup(self):
        self.env['SEND_TIME'] = '18:00'
        before = NOW.replace(hour=9, minute=59)  # 17:59 Shanghai
        self.assertEqual(self.run_daily(now=before)[0], 0)
        self.email.assert_not_called()
        self.assertTrue((self.site/'index.html').exists())
        self.assertEqual(self.run_daily(now=before + timedelta(minutes=1))[0], 0)
        self.email.assert_called_once()
        self.run_daily(now=before + timedelta(minutes=8))
        self.email.assert_called_once()

    def test_default_time_still_1230_without_environment_override(self):
        before = NOW.replace(hour=4, minute=29)
        self.run_daily(now=before)
        self.email.assert_not_called()
        self.run_daily(now=before + timedelta(minutes=1))
        self.email.assert_called_once()

    def test_invalid_send_time_has_no_collection_or_write_side_effects(self):
        for value in ('', '18:0', '6:00', '24:00', '18:60', '18:00\n', ' 18:00', '１８:００'):
            with self.subTest(value=value):
                fetch = MagicMock()
                self.assertEqual(self.run_daily(env=self.env | {'SEND_TIME':value}, fetch=fetch)[0], 1)
                fetch.assert_not_called()
                self.assertFalse(self.path.exists())
                self.assertFalse(self.site.exists())
        self.email.assert_not_called()
        self.renderer.assert_not_called()

    def test_force_send_still_bypasses_server_time(self):
        self.run_daily(env=self.env | {'SEND_TIME':'18:00'}, force_send=True)
        self.email.assert_called_once()

    def test_feishu_one_reminder_covers_full_queue(self):
        self.env['WEB_REMINDER_CHANNEL'] = 'feishu'
        self.assertEqual(self.run_daily(fetch=lambda _: many_feed(25))[0], 0)
        self.email.assert_not_called()
        self.feishu.assert_called_once()
        self.assertEqual(len(self.feishu.call_args.args[0]), 25)
        self.assertTrue(all(i['delivery']['feishu'] == 'sent' for i in daily.load_state(self.path)['items'].values()))

    def test_failure_retains_pending_and_retry(self):
        self.email.side_effect = RuntimeError('SECRET')
        code, messages = self.run_daily()
        self.assertEqual(code, 1)
        self.assertNotIn('SECRET', '\n'.join(messages))
        state = daily.load_state(self.path)
        self.assertIsNone(state['last_sent_dates']['email'])
        self.assertEqual(next(iter(state['items'].values()))['delivery']['email'], 'pending')
        self.email.side_effect = None
        self.run_daily()
        self.assertEqual(self.email.call_count, 2)
        self.feishu.assert_not_called()

    def test_failed_publication_saves_collection_but_never_sends_link(self):
        self.renderer.side_effect = OSError('SECRET')
        code, messages = self.run_daily(force_send=True)
        self.assertEqual(code, 1)
        self.assertNotIn('SECRET', '\n'.join(messages))
        self.assertTrue(daily.load_state(self.path)['initialized'])
        self.assertEqual(next(iter(daily.load_state(self.path)['items'].values()))['delivery']['email'], 'pending')
        self.email.assert_not_called()
        self.feishu.assert_not_called()

    def test_dry_run_has_no_state_site_or_delivery_side_effects(self):
        daily.save_state(self.path, daily.new_state())
        before = self.path.read_bytes()
        self.assertEqual(self.run_daily(dry_run=True, force_send=True)[0], 0)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.site.exists())
        self.renderer.assert_not_called()
        self.email.assert_not_called()
        self.feishu.assert_not_called()

    def test_configuration_rejected_before_collection(self):
        for extra in ({'WEB_OUTPUT_DIR': ''}, {'WEB_BASE_URL': 'javascript:alert(1)'},
                      {'WEB_BASE_URL': 'https://u:p@example.com'}, {'WEB_REMINDER_CHANNEL': 'both'},
                      {'WEB_BASE_URL': 'https://example.com/daily/'}, {'WEB_BASE_URL':'https://example.com/?q=1'}):
            with self.subTest(extra=extra):
                code, _ = self.run_daily(env=self.env | extra)
                self.assertEqual(code, 1)
                self.assertFalse(self.path.exists())
                self.assertFalse(self.site.exists())
        self.email.assert_not_called()
        self.feishu.assert_not_called()

    def test_missing_selected_credentials_fails_before_fetch_or_any_write(self):
        daily.save_state(self.path, daily.new_state())
        before = self.path.read_bytes()
        for extra in ({'SMTP_PASSWORD':''}, {'SMTP_USERNAME':''},
                      {'WEB_REMINDER_CHANNEL':'feishu', 'FEISHU_WEBHOOK':''}):
            with self.subTest(extra=extra):
                fetch = MagicMock()
                self.assertEqual(self.run_daily(env=self.env | extra, fetch=fetch)[0], 1)
                fetch.assert_not_called()
                self.assertEqual(self.path.read_bytes(), before)
                self.assertFalse(self.site.exists())
        self.renderer.assert_not_called()
        self.email.assert_not_called()
        self.feishu.assert_not_called()

    def test_all_sources_failure_preserves_page_bytes_and_timestamp(self):
        self.run_daily()
        state = daily.load_state(self.path)
        stale = next(iter(state['items'].values())).copy()
        stale.update(id='expired', seen_at=(NOW - timedelta(days=100)).isoformat())
        state['items']['expired'] = stale
        daily.save_state(self.path, state)
        target = self.site / 'index.html'
        before = target.read_bytes()
        self.renderer.reset_mock()
        self.email.reset_mock()
        code, messages = self.run_daily(now=NOW + timedelta(days=1),
                                        fetch=MagicMock(side_effect=TimeoutError('secret')))
        self.assertEqual(code, 1)
        self.assertEqual(target.read_bytes(), before)
        self.renderer.assert_not_called()
        self.email.assert_not_called()
        self.feishu.assert_not_called()
        self.assertTrue(any('网页未更新' in message for message in messages))
        self.assertNotIn('expired', daily.load_state(self.path)['items'])

    def test_web_queue_only_counts_published_ids_and_retains_invalid_pending(self):
        state = daily.new_state()
        state['initialized'] = True
        for key, link in [('visible','https://example.com/article?q=1'),
                          ('long','https://example.com/'+'a'*4096),
                          ('credentials','https://user:secret@example.com/')]:
            item = daily.parse_feed(rss(), 'test')[0] | {
                'id':key, 'link':link, 'category':'底盘技术', 'seen_at':NOW.isoformat(),
                'status':'pending', 'delivery':{'email':'pending','feishu':'skipped'}}
            state['items'][key] = item
        daily.save_state(self.path, state)
        code, messages = self.run_daily(fetch=lambda _: b'<rss><channel/></rss>')
        self.assertEqual(code, 0)
        self.assertEqual([item['id'] for item in self.email.call_args.args[0]], ['visible'])
        self.assertEqual([item['id'] for item in self.renderer.call_args.args[0]['articles']], ['visible'])
        saved = daily.load_state(self.path)
        self.assertEqual(saved['items']['visible']['delivery']['email'], 'sent')
        self.assertEqual(saved['items']['long']['delivery']['email'], 'pending')
        self.assertEqual(saved['items']['credentials']['delivery']['email'], 'pending')
        self.assertTrue(any('2 篇未在网页展示' in message for message in messages))
        self.run_daily(now=NOW + timedelta(days=1), fetch=lambda _: b'<rss><channel/></rss>')
        self.email.assert_called_once()
        self.assertEqual(daily.load_state(self.path)['last_sent_dates']['email'], '2026-11-18')

    def test_output_only_keeps_legacy_channels_and_skipped_never_replays(self):
        output_only = {'WEB_OUTPUT_DIR': str(self.site)}
        self.run_daily(env=output_only)
        self.assertEqual(len(self.renderer.call_args.args[0]['articles']), 1)
        self.run_daily(force_send=True)
        self.email.assert_not_called()
        self.feishu.assert_not_called()
        self.run_daily(env={k:v for k,v in self.env.items() if k != 'WEB_BASE_URL'},
                       fetch=lambda _: rss(guid='new', link='https://example.com/new'))
        self.email.assert_called_once()
        self.feishu.assert_called_once()

    def test_main_uses_persistent_state_environment_path(self):
        with patch.dict(os.environ, {'DAILY_STATE_PATH': str(self.path)}, clear=True), \
                patch.object(sys, 'argv', ['daily.py', '--dry-run']), \
                patch('daily.run', return_value=(0, ['preview'])) as runner, \
                patch('builtins.print'):
            self.assertEqual(daily.main(), 0)
        self.assertEqual(runner.call_args.args[1], self.path)


class ReminderTransportTests(unittest.TestCase):
    def setUp(self):
        self.items = [daily.parse_feed(rss(guid=str(i)), 'source')[0] | {'category':'底盘技术'} for i in range(25)]
        self.env = {'WEB_BASE_URL':'https://example.com/index.html', 'SMTP_USERNAME':'test@example.com',
                    'SMTP_PASSWORD':'secret', 'FEISHU_WEBHOOK':WEBHOOK, 'FEISHU_SIGN_SECRET':'test-secret'}

    @patch('daily.smtplib.SMTP_SSL')
    def test_email_short_multipart_reminder(self, smtp):
        smtp.return_value.__enter__.return_value.send_message.return_value = {}
        daily.send_mail(self.items, '2026-11-18', self.env)
        message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        text = message.get_body(preferencelist=('plain',)).get_content()
        markup = message.get_body(preferencelist=('html',)).get_content()
        self.assertIn('本期 25 篇', text)
        self.assertIn('底盘技术 25 篇', text)
        self.assertIn('汽车新技术 0 篇', text)
        self.assertNotIn(self.items[0]['summary'], text)
        self.assertIn('href="https://example.com/index.html"', markup)
        self.assertIn(self.env['WEB_BASE_URL'], text)

    @patch('daily.build_opener')
    def test_feishu_full_queue_one_signed_bounded_message(self, builder):
        response = builder.return_value.open.return_value.__enter__.return_value
        response.status, response.read.return_value = 200, b'{"code":0}'
        daily.send_feishu(self.items, '2026-11-18', self.env)
        builder.return_value.open.assert_called_once()
        raw = builder.return_value.open.call_args.args[0].data
        payload = json.loads(raw)
        self.assertLess(len(raw), 20000)
        self.assertIn('本期 25 篇', payload['content']['text'])
        self.assertIn('sign', payload)
        self.assertNotIn(self.items[0]['summary'], payload['content']['text'])
        response.read.return_value = b'{"code":19024}'
        with self.assertRaises(RuntimeError):
            daily.send_feishu(self.items, '2026-11-18', self.env)

    @patch('daily.build_opener')
    def test_feishu_reminder_byte_limit_still_enforced(self, builder):
        with patch('daily.feishu_payload', return_value=b'x' * 20000), self.assertRaises(ValueError):
            daily.send_feishu(self.items, '2026-11-18', self.env)
        builder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
