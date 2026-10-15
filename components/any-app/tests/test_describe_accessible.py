#!/usr/bin/env python3
"""Dependency-free tests for compact AT-SPI description and safe selection."""
import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'any_app' / 'describe_accessible.py'
spec = importlib.util.spec_from_file_location('describe_accessible', SCRIPT)
describe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(describe)


class FakeState:
    def __init__(self, states): self.states = set(states)
    def contains(self, state): return state in self.states


class FakeRect:
    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


class FakeComponent:
    def __init__(self, rect, window_rect=None):
        self.rect, self.window_rect = rect, window_rect
    def getExtents(self, coords):
        return self.window_rect if coords == 1 else self.rect


class FakeNode:
    def __init__(self, role, name='', children=(), rect=None, states=(), window_rect=None):
        self.role, self.name, self._children = role, name, list(children)
        self.rect, self.window_rect, self._states = rect, window_rect, set(states)
        self.childCount = len(self._children)
    def getRoleName(self): return self.role
    def getChildAtIndex(self, index): return self._children[index]
    def getState(self): return FakeState(self._states)
    def queryComponent(self):
        if self.rect is None: raise RuntimeError('no component')
        return FakeComponent(self.rect, self.window_rect)


class FakeAtspi:
    DESKTOP_COORDS = 0
    WINDOW_COORDS = 1
    STATE_SHOWING = 'showing'
    STATE_ENABLED = 'enabled'
    STATE_FOCUSED = 'focused'


class AccessibleDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.button = FakeNode('push button', 'Save', rect=FakeRect(10, 20, 60, 25),
                               window_rect=FakeRect(3, 4, 60, 25),
                               states=('showing', 'enabled'))
        self.label = FakeNode('label', 'Document title', rect=FakeRect(8, 4, 100, 18))
        self.window = FakeNode('frame', 'Editor - Draft', [self.label, self.button],
                               rect=FakeRect(0, 0, 800, 600), states=('showing', 'focused'))
        self.app = FakeNode('application', 'Editor', [self.window])
        self.other = FakeNode('frame', 'Calculator', rect=FakeRect(20, 20, 200, 200))
        self.desktop = FakeNode('desktop frame', 'Desktop', [self.app, self.other])

    def test_compact_description_includes_geometry_states_and_path(self):
        result = describe.build_description(self.desktop, FakeAtspi, 'draft')
        self.assertEqual(result['source'], 'at-spi')
        self.assertEqual(result['geometry'], {
            'coordinate_space': 'atspi-desktop-coords',
            'secondary_coordinate_space': 'atspi-window-coords',
            'screen_alignment': 'unverified'})
        self.assertEqual(result['window']['title'], 'Editor - Draft')
        self.assertEqual(result['window']['desktop_path'], '0/0')
        self.assertEqual(result['elements'][2]['name'], 'Save')
        self.assertEqual(result['elements'][2]['bounds'],
                         {'x': 10, 'y': 20, 'width': 60, 'height': 25})
        self.assertEqual(result['elements'][2]['window_bounds'],
                         {'x': 3, 'y': 4, 'width': 60, 'height': 25})
        self.assertEqual(result['elements'][2]['states'], ['showing', 'enabled'])
        self.assertEqual(result['elements'][2]['visibility'], 'showing')
        self.assertFalse(result['truncated'])

    def test_ambiguous_and_missing_titles_fail_closed(self):
        self.desktop._children.append(FakeNode('frame', 'Editor - Other'))
        self.desktop.childCount += 1
        with self.assertRaisesRegex(LookupError, 'ambiguous'):
            describe.build_description(self.desktop, FakeAtspi, 'editor')
        with self.assertRaisesRegex(LookupError, 'no matching'):
            describe.build_description(self.desktop, FakeAtspi, 'not there')
        with self.assertRaisesRegex(ValueError, 'must not be empty'):
            describe.build_description(self.desktop, FakeAtspi, '  ')

    def test_caps_element_walk_and_flags_truncation(self):
        result = describe.build_description(self.desktop, FakeAtspi, 'draft', max_elements=2)
        self.assertEqual(len(result['elements']), 2)
        self.assertTrue(result['truncated'])

    def test_missing_component_is_explicit_null_geometry(self):
        no_geom = FakeNode('label', 'No geometry')
        self.window._children = [no_geom]
        self.window.childCount = 1
        result = describe.build_description(self.desktop, FakeAtspi, 'draft')
        self.assertIsNone(result['elements'][1]['bounds'])

    def test_accessible_text_normalization_work_is_capped(self):
        huge = "界" * (describe.MAX_ACCESSIBLE_TEXT_INPUT * 100)
        normalized = describe.display_text(huge)
        self.assertEqual(len(normalized), describe.MAX_ACCESSIBLE_TEXT_INPUT)
        self.assertTrue(normalized.endswith("…"))
        self.assertEqual(normalized[:-1], huge[:describe.MAX_ACCESSIBLE_TEXT_INPUT - 1])

    def test_invalid_unicode_from_accessible_names_is_replaced_before_json(self):
        # AT-SPI text is untrusted input. Lone surrogates are not UTF-8
        # encodable and used to defeat the response-size/serialization contract.
        malformed = FakeNode('push button\ud800', 'Save\udfff', rect=FakeRect(1, 2, 3, 4))
        self.window._children = [malformed]
        self.window.childCount = 1
        description = describe.build_description(self.desktop, FakeAtspi, 'draft')
        encoded = describe.serialize_description(description, max_bytes=4096)
        payload = json.loads(encoded)
        self.assertEqual(payload['elements'][1]['name'], 'Save�')
        self.assertEqual(payload['elements'][1]['role'], 'push button�')
        self.assertLessEqual(len(encoded.encode('utf-8')), 4096)
        self.assertNotIn('\\ud800', encoded)
        self.assertNotIn('\\udfff', encoded)

    def test_malformed_geometry_is_unknown_not_lossily_coerced(self):
        for bad in (True, 1.5, '12', None):
            with self.subTest(value=bad):
                node = FakeNode('push button', 'Malformed geometry',
                                rect=FakeRect(bad, 2, 30, 10))
                self.assertIsNone(describe.bounds(node, FakeAtspi))

        class IntegerLike:
            def __index__(self): return 12
        node = FakeNode('push button', 'Integer coordinates',
                        rect=FakeRect(IntegerLike(), 2, 30, 10))
        self.assertEqual(describe.bounds(node, FakeAtspi),
                         {'x': 12, 'y': 2, 'width': 30, 'height': 10})

    def test_description_uses_null_window_bounds_when_coordinate_constant_missing(self):
        class DesktopOnlyAtspi:
            DESKTOP_COORDS = 0
            STATE_SHOWING = FakeAtspi.STATE_SHOWING
            STATE_ENABLED = FakeAtspi.STATE_ENABLED
            STATE_FOCUSED = FakeAtspi.STATE_FOCUSED

        result = describe.build_description(self.desktop, DesktopOnlyAtspi, 'draft')
        self.assertIsNone(result['elements'][2]['window_bounds'])

    def test_bounds_select_coordinate_space_and_fail_closed_when_unavailable(self):
        node = FakeNode('push button', 'Move', rect=FakeRect(70, 80, 20, 10),
                        window_rect=FakeRect(5, 6, 20, 10))
        self.assertEqual(describe.bounds(node, FakeAtspi, 'desktop'),
                         {'x': 70, 'y': 80, 'width': 20, 'height': 10})
        self.assertEqual(describe.bounds(node, FakeAtspi, 'window'),
                         {'x': 5, 'y': 6, 'width': 20, 'height': 10})
        self.assertIsNone(describe.bounds(node, type('NoWindowCoords', (), {
            'DESKTOP_COORDS': 0})(), 'window'))
        with self.assertRaisesRegex(ValueError, 'coordinate_space'):
            describe.bounds(node, FakeAtspi, 'screen')

    def test_negative_dimensions_fail_closed(self):
        for size in (-1, -2):
            node = FakeNode('panel', 'Invalid size',
                            rect=FakeRect(0, 0, size, 10))
            self.assertIsNone(describe.bounds(node, FakeAtspi))

    def test_accessibility_tree_errors_mark_incomplete_and_continue(self):
        class PartlyBroken(FakeNode):
            def getChildAtIndex(self, index):
                if index == 0: raise RuntimeError('child became defunct')
                return super().getChildAtIndex(index)

        good = FakeNode('push button', 'Still readable')
        parent = PartlyBroken('panel', 'Changing children', [FakeNode('label'), good])
        self.window._children = [parent]
        self.window.childCount = 1
        elements, truncated, _ = describe.describe_window(self.window, FakeAtspi)
        self.assertTrue(truncated)
        self.assertEqual([item['name'] for item in elements],
                         ['Editor - Draft', 'Changing children', 'Still readable'])
        # Selection fails closed when the live desktop scan encounters that broken branch.
        with self.assertRaisesRegex(LookupError, 'scan incomplete'):
            describe.build_description(self.desktop, FakeAtspi, 'draft')

        class BrokenCount:
            name = 'Incomplete desktop'
            def getRoleName(self): return 'desktop frame'
            @property
            def childCount(self): raise RuntimeError('defunct accessible')

        _, visited, scan_truncated = describe.find_windows(BrokenCount())
        self.assertEqual(visited, 1)
        self.assertTrue(scan_truncated)
        with self.assertRaisesRegex(LookupError, 'scan incomplete'):
            describe.build_description(BrokenCount(), FakeAtspi, 'anything')

        broken_window = FakeNode('frame', 'Broken window')
        del broken_window.childCount
        broken_desktop = FakeNode('desktop frame', 'Desktop', [broken_window])
        with self.assertRaisesRegex(LookupError, 'scan incomplete'):
            describe.build_description(broken_desktop, FakeAtspi, 'Broken window')
        elements, tree_incomplete, _ = describe.describe_window(broken_window, FakeAtspi)
        self.assertEqual(len(elements), 1)
        self.assertTrue(tree_incomplete)

    def test_visibility_is_tristate_when_state_is_missing_or_unreadable(self):
        class BrokenState(FakeNode):
            def getState(self): raise RuntimeError('state unavailable')

        class UnreadableShowing(FakeState):
            def contains(self, state):
                if state == 'showing': raise RuntimeError('state set defunct')
                return super().contains(state)

        class PartlyReadableState(FakeNode):
            def getState(self): return UnreadableShowing(('enabled',))

        class NoStateAPI:
            pass

        for node in (NoStateAPI(), BrokenState('label', 'Error'),
                     PartlyReadableState('label', 'Partial')):
            states, visibility = describe.state_snapshot(node, FakeAtspi)
            self.assertEqual(visibility, 'unknown')

        not_showing = FakeNode('label', 'Hidden', states=('enabled',))
        states, visibility = describe.state_snapshot(not_showing, FakeAtspi)
        self.assertEqual(visibility, 'not_showing')
        self.assertEqual(states, ['enabled'])

        class NoShowingConstant:
            STATE_ENABLED = 'enabled'
        states, visibility = describe.state_snapshot(not_showing, NoShowingConstant)
        self.assertEqual(visibility, 'unknown')
        self.assertEqual(states, ['enabled'])

    def test_non_integer_child_count_is_incomplete_not_coerced(self):
        class MalformedCount:
            def __init__(self, value):
                self.value = value
            @property
            def childCount(self):
                return self.value

        # bool is an int subclass in Python; floats and numeric strings also
        # previously slipped through int(...) and could misstate completeness.
        for value in (True, False, 1.5, "2", None):
            with self.subTest(value=value):
                self.assertEqual(describe.child_count(MalformedCount(value)), (0, True))
                children, incomplete = describe.children(MalformedCount(value))
                self.assertEqual(children, ())
                self.assertTrue(incomplete)

                # Invalid metadata anywhere in the desktop scan must prevent a
                # seemingly unique title match from being trusted.
                desktop = FakeNode('desktop frame', 'Desktop', [
                    MalformedCount(value), FakeNode('frame', 'Editor - Draft')])
                with self.assertRaisesRegex(LookupError, 'scan incomplete'):
                    describe.build_description(desktop, FakeAtspi, 'draft')

        class IntegerLike:
            def __index__(self):
                return 2
        self.assertEqual(describe.child_count(MalformedCount(IntegerLike())), (2, False))

    def test_negative_child_count_is_incomplete_not_empty(self):
        class InvalidCount:
            def __init__(self, role='application', name='Changing tree'):
                self.role, self.name = role, name
            @property
            def childCount(self):
                return -1
            def getRoleName(self):
                return self.role

        invalid = InvalidCount()
        self.assertEqual(describe.child_count(invalid), (0, True))
        children, incomplete = describe.children(invalid)
        self.assertEqual(children, ())
        self.assertTrue(incomplete)

        # A matching window elsewhere cannot be selected while an unknown
        # desktop branch might contain a duplicate title.
        broken_desktop = FakeNode('desktop frame', 'Desktop', [
            InvalidCount('application', 'Broken branch'),
            FakeNode('frame', 'Editor - Draft'),
        ])
        with self.assertRaisesRegex(LookupError, 'scan incomplete'):
            describe.build_description(broken_desktop, FakeAtspi, 'draft')

    def test_scan_depth_and_size_are_bounded(self):
        deep = FakeNode('application', 'deep', [self.window])
        for _ in range(60):
            deep = FakeNode('panel', children=[deep])
        matches, visited, truncated = describe.find_windows(deep, max_nodes=12, max_depth=5)
        self.assertEqual(matches, [])
        self.assertLessEqual(visited, 12)
        self.assertTrue(truncated)

    def test_extreme_fanout_is_capped_before_scheduling_children(self):
        huge = FakeNode('application', 'many',
                        [FakeNode('frame', f'Window {i}') for i in range(25000)])
        matches, visited, truncated = describe.find_windows(huge, max_nodes=20)
        self.assertEqual(visited, 20)
        self.assertLessEqual(len(matches), 19)
        self.assertTrue(truncated)

    def test_json_serializer_enforces_utf8_byte_budget_and_marks_truncation(self):
        description = describe.build_description(self.desktop, FakeAtspi, 'draft')
        encoded = describe.serialize_description(description, max_bytes=800)
        payload = json.loads(encoded)
        self.assertLessEqual(len(encoded.encode('utf-8')), 800)
        self.assertTrue(payload['output_truncated'])
        self.assertTrue(payload['truncated'])
        self.assertEqual(payload['counts']['emitted_elements'], len(payload['elements']))
        self.assertLess(len(payload['elements']), len(description['elements']))

    def test_json_serializer_enforces_utf8_bytes_for_long_unicode_names(self):
        description = describe.build_description(self.desktop, FakeAtspi, 'draft')
        description['elements'] = list(description['elements']) + [
            {'id': f'root/{i}', 'role': 'label', 'name': '界🙂' * 400,
             'bounds': None, 'window_bounds': None, 'states': [], 'visibility': 'unknown'}
            for i in range(4)
        ]
        encoded = describe.serialize_description(description, max_bytes=1200)
        payload = json.loads(encoded)
        self.assertLessEqual(len(encoded.encode('utf-8')), 1200)
        self.assertTrue(payload['output_truncated'])
        self.assertEqual(payload['counts']['emitted_elements'], len(payload['elements']))
        self.assertLess(len(payload['elements']), len(description['elements']))

    def test_json_serializer_preserves_complete_output_when_it_fits(self):
        description = describe.build_description(self.desktop, FakeAtspi, 'draft')
        encoded = describe.serialize_description(description, max_bytes=10000)
        payload = json.loads(encoded)
        self.assertFalse(payload['output_truncated'])
        self.assertEqual(payload['counts']['emitted_elements'], len(payload['elements']))
        self.assertEqual(payload['elements'], description['elements'])

    def test_json_serializer_rejects_unrepresentable_metadata_and_bad_budget(self):
        description = describe.build_description(self.desktop, FakeAtspi, 'draft')
        with self.assertRaisesRegex(ValueError, 'too small'):
            describe.serialize_description(description, max_bytes=1)
        for budget in (True, 0, -1, 1.5):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                describe.serialize_description(description, max_bytes=budget)

    def test_cli_emits_compact_json_for_unique_window(self):
        fake_module = type('FakePyAtspiModule', (), {
            'DESKTOP_COORDS': FakeAtspi.DESKTOP_COORDS,
            'WINDOW_COORDS': FakeAtspi.WINDOW_COORDS,
            'STATE_SHOWING': FakeAtspi.STATE_SHOWING,
            'STATE_ENABLED': FakeAtspi.STATE_ENABLED,
            'STATE_FOCUSED': FakeAtspi.STATE_FOCUSED,
            'Registry': type('Registry', (), {
                'getDesktop': staticmethod(lambda index: self.desktop)
            }),
        })()
        stdout = io.StringIO()
        with mock.patch.dict(sys.modules, {'pyatspi': fake_module}), contextlib.redirect_stdout(stdout):
            status = describe.main(['Draft', '--max-elements', '10'])
        self.assertEqual(status, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload['window']['title'], 'Editor - Draft')
        self.assertEqual(payload['elements'][2]['name'], 'Save')
        # The CLI's public bound includes its final newline, not only JSON bytes.
        stdout = io.StringIO()
        with mock.patch.dict(sys.modules, {'pyatspi': fake_module}), contextlib.redirect_stdout(stdout):
            status = describe.main(['Draft', '--max-bytes', '600'])
        self.assertEqual(status, 0)
        self.assertLessEqual(len(stdout.getvalue().encode('utf-8')), 600)

    def test_cli_missing_bindings_fails_with_actionable_error(self):
        stderr = io.StringIO()
        with mock.patch.dict(sys.modules, {'pyatspi': None}), contextlib.redirect_stderr(stderr):
            status = describe.main(['Draft'])
        self.assertEqual(status, 2)
        self.assertIn('AT-SPI bindings unavailable', stderr.getvalue())

    def test_cli_bad_element_or_byte_bound_uses_argparse_error(self):
        for args in (['Draft', '--max-elements', '10001'],
                     ['Draft', '--max-bytes', '100'],
                     ['Draft', '--max-bytes', '1048577']):
            with self.subTest(args=args), self.assertRaises(SystemExit) as raised:
                describe.main(args)
            self.assertEqual(raised.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
