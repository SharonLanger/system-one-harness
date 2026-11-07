import json

from log_utils import capture_records, log


def test_scoped_capture_avoids_file_and_resets(tmp_path, monkeypatch):
    import log_utils

    destination = tmp_path / 'debug.jsonl'
    monkeypatch.setattr(log_utils, 'LOG_PATH', destination)
    records = []
    with capture_records(records.append):
        log('INFO', 'test', 'captured', value=42)
    assert [(r['component'], r['event'], r['details']) for r in records] == [
        ('test', 'captured', {'value': 42})]
    assert not destination.exists()
    log('INFO', 'test', 'default')
    assert json.loads(destination.read_text())['event'] == 'default'
