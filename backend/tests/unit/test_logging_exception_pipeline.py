"""Regression: real exception tuples must survive until traceback formatting."""
import json
import logging
import sys

import pytest
import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.core.logging import configure_logging, get_logger


@pytest.fixture
def logging_state():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    configuration = structlog.get_config().copy()
    logger_states = {name: (logging.getLogger(name).handlers[:], logging.getLogger(name).propagate)
                     for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access', 'celery')}
    levels = {name: logging.getLogger(name).level for name in ('httpx', 'httpcore')}
    clear_contextvars()
    yield
    root.handlers[:], root.level = handlers, level
    for name, (saved, propagate) in logger_states.items():
        logging.getLogger(name).handlers[:] = saved
        logging.getLogger(name).propagate = propagate
    for name, saved in levels.items():
        logging.getLogger(name).setLevel(saved)
    structlog.configure(**configuration)
    clear_contextvars()


def exception_chain():
    try:
        raise RuntimeError('provider password=synthetic-inner')
    except RuntimeError as inner:
        try:
            raise ValueError('wrapper api_key=synthetic-outer') from inner
        except ValueError:
            return sys.exc_info()


@pytest.mark.parametrize('source', ['structlog', 'stdlib', 'celery'])
@pytest.mark.parametrize('form', ['tuple', 'exception'])
def test_delayed_exception_chain_keeps_json_and_redacts_secrets(logging_state, capsys, source, form):
    configure_logging(service='worker')
    bind_contextvars(request_id='fallback022-unit', task_id='synthetic-task')
    info = exception_chain()  # No currently handled exception at emission time.
    logger = get_logger('test') if source == 'structlog' else logging.getLogger('celery.app.trace' if source == 'celery' else 'test.foreign')
    kwargs = {'exc_info': info if form == 'tuple' else info[1]}
    if source == 'structlog':
        kwargs.update(error_code='AI.UNAVAILABLE', nested={'password': 'synthetic-nested'}, other=info[1])
    logger.error('AI.UNAVAILABLE failure', **kwargs)
    captured = capsys.readouterr()
    assert captured.err == '', 'No logging error or unredacted stderr fallback'
    records = [json.loads(line) for line in captured.out.splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record['request_id'] == 'fallback022-unit' and record['task_id'] == 'synthetic-task'
    assert record['service'] == 'worker' and record['level'] == 'error'
    assert 'RuntimeError:' in record['exception'] and 'ValueError:' in record['exception']
    assert 'direct cause' in record['exception']
    for secret in ('synthetic-inner', 'synthetic-outer', 'synthetic-nested'):
        assert secret not in captured.out
    assert 'AI.UNAVAILABLE' in captured.out and '[redacted]' in captured.out


@pytest.mark.parametrize('source', ['structlog', 'stdlib', 'celery'])
def test_active_exception_true_and_normal_messages(logging_state, capsys, source):
    configure_logging(service='worker')
    bind_contextvars(request_id='fallback022-unit', task_id='synthetic-task')
    logger = get_logger('test') if source == 'structlog' else logging.getLogger('celery.app.trace' if source == 'celery' else 'test.foreign')
    try:
        raise RuntimeError('password=synthetic-active')
    except RuntimeError:
        logger.error('failed', exc_info=True)
    logger.info('normal %s', 'password=synthetic-positional')
    captured = capsys.readouterr()
    assert captured.err == ''
    records = [json.loads(line) for line in captured.out.splitlines()]
    assert len(records) == 2 and 'RuntimeError:' in records[0]['exception']
    assert 'exception' not in records[1]
    assert 'synthetic-active' not in captured.out and 'synthetic-positional' not in captured.out
