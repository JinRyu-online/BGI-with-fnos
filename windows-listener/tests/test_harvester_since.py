"""LogHarvester.since(seq) 增量读取测试（WS burst 丢行修复）。

直接操作内部状态（不依赖收割线程时序）验证序号推进与丢失占位逻辑。
"""
from bgi_trigger.core.log_harvester import LogHarvester


def _make(max_lines=50) -> LogHarvester:
    h = LogHarvester(job_id="sinceJob", log_path="C:/nonexistent/nope.log",
                     max_lines=max_lines)
    # 不启动收割线程：直接以与 _run 相同的锁语义灌入缓冲
    return h


def _feed(h: LogHarvester, lines: list[str]) -> None:
    """与 _run 收割线程相同的锁语义灌入缓冲（先算溢出再 extend）。"""
    with h._lock:
        overflow = 0
        if h._buf.maxlen is not None:
            overflow = max(0, len(h._buf) + len(lines) - h._buf.maxlen)
        h._buf.extend(lines)
        h._total_lines += len(lines)
        if overflow > 0:
            h._dropped_lines += overflow


def test_since_from_zero_returns_all_and_advances_seq():
    h = _make()
    _feed(h, ["a", "b", "c"])

    lines, seq = h.since(0)

    assert lines == ["a", "b", "c"]
    assert seq == 3


def test_since_incremental_advances_correctly():
    h = _make()
    _feed(h, ["a", "b", "c"])

    lines, seq = h.since(0)
    assert lines == ["a", "b", "c"] and seq == 3

    _feed(h, ["d", "e"])
    lines, seq = h.since(seq)
    assert lines == ["d", "e"] and seq == 5

    # 无新行 → 空列表，seq 不变
    lines, seq2 = h.since(seq)
    assert lines == [] and seq2 == 5


def test_since_middle_offset():
    h = _make()
    _feed(h, ["1", "2", "3", "4", "5"])

    lines, seq = h.since(2)   # 要序号 > 2 的行 → "3","4","5"

    assert lines == ["3", "4", "5"]
    assert seq == 5


def test_since_seq_at_head_returns_nothing():
    h = _make()
    _feed(h, ["x"])

    lines, seq = h.since(1)   # 已读到最新

    assert lines == [] and seq == 1


def test_since_overflow_returns_placeholder_with_all_buffer():
    """客户端落后超过缓冲容量 → 全量缓冲 + 首部丢失占位。"""
    h = _make(max_lines=5)
    _feed(h, ["l1", "l2", "l3", "l4", "l5", "l6", "l7", "l8"])
    # dropped = 3（l1..l3 被挤出），缓冲 = l4..l8
    assert h._dropped_lines == 3

    # 客户端 seq=1（只读到 l1，落后 dropped-1=2 行）
    lines, seq = h.since(1)

    assert seq == 8
    assert lines[0] == "⚠ 已丢失 2 行（超出缓冲容量）"
    assert lines[1:] == ["l4", "l5", "l6", "l7", "l8"]


def test_since_overflow_far_behind_counts_all_dropped():
    """seq=0 且缓冲已翻转：丢失数 = dropped - 0。"""
    h = _make(max_lines=3)
    _feed(h, [str(i) for i in range(1, 8)])   # 7 行，dropped=4，缓冲=5,6,7

    lines, seq = h.since(0)

    assert h._dropped_lines == 4
    assert lines[0] == "⚠ 已丢失 4 行（超出缓冲容量）"
    assert lines[1:] == ["5", "6", "7"]
    assert seq == 7


def test_since_within_buffer_no_placeholder_even_after_overflow():
    """缓冲翻转过但客户端仍跟得上（seq >= dropped）→ 无占位、正常增量。"""
    h = _make(max_lines=3)
    _feed(h, ["1", "2", "3", "4", "5"])   # dropped=2，缓冲=3,4,5

    lines, seq = h.since(4)   # 客户端已读到 l4（序号4）→ 只要 l5

    assert lines == ["5"]
    assert seq == 5
