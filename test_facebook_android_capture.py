import unittest
from unittest.mock import patch, Mock
from facebook_android_capture import (
    CaptureError, nodes_from_xml, reaction_entry, visibility_limited,
    progress_state, parse_args, validate_options, CaptureRunner,
)


def ui_node(label, resource='feed_like_count', long_click='false'):
    return nodes_from_xml('<hierarchy><node package="com.facebook.katana" '
        'enabled="true" clickable="true" bounds="[0,0][200,100]" '
        f'long-clickable="{long_click}" resource-id="com.facebook.katana:id/{resource}" '
        f'text="{label}" /></hierarchy>')[0]


class AndroidCaptureContracts(unittest.TestCase):
    def test_cleanup_restores_only_owned_proxy_and_children(self):
        args = parse_args(['--serial', 'emulator-5554', '--post-url',
                          'https://www.facebook.com/123/posts/456', '--output-dir', 'runs/test'])
        runner = CaptureRunner(args)
        runner.old_proxy = ':0'
        runner.proxy_dirty = runner.reverse_owned = True
        runner.adb = Mock(side_effect=['127.0.0.1:18081', '', ':0', ''])
        child = Mock()
        child.poll.return_value = None
        runner.children = [child]
        runner.cleanup()
        self.assertEqual(runner.cleanup_errors, [])
        runner.adb.assert_any_call('shell', 'settings', 'put', 'global', 'http_proxy', ':0')
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=8)

    def test_cleanup_preserves_concurrent_proxy_change(self):
        args = parse_args(['--serial', 'emulator-5554', '--post-url',
                          'https://www.facebook.com/123/posts/456', '--output-dir', 'runs/test'])
        runner = CaptureRunner(args)
        runner.old_proxy, runner.proxy_dirty = ':0', True
        runner.adb = Mock(return_value='127.0.0.1:19999')
        runner.cleanup()
        self.assertEqual(runner.adb.call_count, 1)
        self.assertIn('restore Android http_proxy', runner.cleanup_errors)

    def test_never_taps_like_action_instead_of_count(self):
        for label in ('Like', 'Liked', '赞', 'Like button'):
            with self.subTest(label=label), self.assertRaises(CaptureError):
                reaction_entry([ui_node(label)])
        self.assertEqual(reaction_entry([ui_node('334')])['label'], '334')

    def test_ambiguous_counts_are_rejected(self):
        with self.assertRaises(CaptureError):
            reaction_entry([ui_node('334'), ui_node('63')])

    def test_browser_observed_visibility_restriction(self):
        text = ('You can see the total number of people who reacted to this post, '
                'but only the names of friends who have reacted will be shown.')
        self.assertTrue(visibility_limited([{'label': text}]))
        self.assertFalse(visibility_limited([{'label': '334 reactions'}]))

    def test_no_records_never_mean_terminal(self):
        self.assertEqual(progress_state([], '123')[1], 'pending')

    def test_wrong_source_and_throttling_override_terminal(self):
        with patch('facebook_android_capture.summarize_reaction_records', return_value={
                'observed_source_post_ids': ['other'], 'terminal_page_seen': True}):
            self.assertEqual(progress_state([], '123')[1], 'wrong-source')
        with patch('facebook_android_capture.summarize_reaction_records', return_value={
                'observed_source_post_ids': ['123'], 'rate_limit_count': 1, 'terminal_page_seen': True}):
            self.assertEqual(progress_state([], '123')[1], 'throttled')

    def test_regular_mode_and_invalid_pcap_fail_before_device_use(self):
        args = parse_args(['--serial', 'emulator-5554', '--post-url',
                          'https://www.facebook.com/watch/?v=1069930052107630',
                          '--output-dir', 'runs/test'])
        self.assertEqual(validate_options(args, {'FACEBOOK_NETWORK_MODE': 'direct'})[1], 'regular')
        args.save_artifacts = 'pcap'
        with self.assertRaisesRegex(CaptureError, 'PCAP unsupported'):
            validate_options(args, {'FACEBOOK_NETWORK_MODE': 'direct'})


if __name__ == '__main__':
    unittest.main()
