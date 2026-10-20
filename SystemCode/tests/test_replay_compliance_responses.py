"""Offline replay must bind saved responses to their recorded case IDs."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from replay_compliance_responses import load_raw_responses


class ReplayResponseTests(unittest.TestCase):
    def fixture(self, directory):
        event = dict(clause_id='T001', response_id='response-one', model='saved-model')
        payload = dict(tender_slots={}, bid_slots={})
        envelope = dict(id='response-one', model='saved-model',
                        choices=[dict(finish_reason='stop', message=dict(content=json.dumps(payload)))])
        self.write(directory/'api_calls.json', [event])
        self.write(directory/'raw_T001_001.json', envelope)
        return event, envelope

    def write(self, path, data):
        path.write_text(json.dumps(data), encoding='utf-8')

    def test_uses_raw_payload_with_no_network_or_grounded_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.fixture(directory)
            self.write(directory/'cache.json', {'old_grounding_issues':['obsolete']})
            with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
                payloads, hashes = load_raw_responses(directory, [dict(clause_id='T001')])
            self.assertEqual(payloads['T001'][0], dict(tender_slots={}, bid_slots={}))
            self.assertEqual(len(hashes['raw_T001_001.json']), 64)

    def test_response_mismatch_or_truncation_is_rejected(self):
        for field, value in [('id','other-response'), ('model','other-model'), ('finish_reason','length')]:
            with tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                _, envelope = self.fixture(directory)
                if field == 'finish_reason':
                    envelope['choices'][0][field] = value
                else:
                    envelope[field] = value
                self.write(directory/'raw_T001_001.json', envelope)
                with self.assertRaises(ValueError):
                    load_raw_responses(directory, [dict(clause_id='T001')])

    def test_missing_or_repeated_response_is_not_silently_selected(self):
        for events in [[], ['duplicate'], ['different']]:
            with tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                event, _ = self.fixture(directory)
                altered = [] if not events else [event,event] if events == ['duplicate'] else [dict(event,clause_id='T002')]
                self.write(directory/'api_calls.json', altered)
                with self.assertRaises(ValueError):
                    load_raw_responses(directory, [dict(clause_id='T001')])


if __name__ == '__main__':
    unittest.main()
