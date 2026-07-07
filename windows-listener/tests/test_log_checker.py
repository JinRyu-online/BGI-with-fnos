from pathlib import Path

from execution import make_log_checker


def test_log_checker_none_when_path_empty(tmp_path):
    checker = make_log_checker(log_path="", keyword="done")
    assert checker is None


def test_log_checker_none_when_keyword_empty(tmp_path):
    log = tmp_path / "bgi.log"
    log.write_text("x", encoding="utf-8")
    checker = make_log_checker(log_path=str(log), keyword="")
    assert checker is None


def test_log_checker_returns_true_when_keyword_present(tmp_path):
    log = tmp_path / "bgi.log"
    log.write_text("启动中...\n调度组执行完成\n", encoding="utf-8")

    checker = make_log_checker(str(log), "调度组执行完成")
    assert checker() is True


def test_log_checker_returns_false_when_keyword_absent(tmp_path):
    log = tmp_path / "bgi.log"
    log.write_text("启动中...\n", encoding="utf-8")

    checker = make_log_checker(str(log), "调度组执行完成")
    assert checker() is False


def test_log_checker_picks_up_keyword_appended_later(tmp_path):
    log = tmp_path / "bgi.log"
    log.write_text("启动中...\n", encoding="utf-8")

    checker = make_log_checker(str(log), "调度组执行完成")
    assert checker() is False

    with open(log, "a", encoding="utf-8") as f:
        f.write("调度组执行完成\n")

    assert checker() is True
