"""
test_kanban_manager.py — KanbanDB.add_card smoke test
======================================================
- import 검증
- add_card → list_cards → move_card → delete_card 기본 동작 확인
"""

import importlib.util
import pytest
from unittest.mock import MagicMock
from pathlib import Path

MODULE_PATH = "/Users/bluesea/Applications/Mjauto/Scripts/modules/kanban_manager.py"


def _import_kanban():
    spec = importlib.util.spec_from_file_location("kanban_manager", MODULE_PATH)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Import ──────────────────────────────────────────────────

def test_import():
    """kanban_manager import 성공"""
    mod = _import_kanban()
    assert hasattr(mod, "KanbanDB")
    assert hasattr(mod, "KANBAN_DB_PATH")
    assert hasattr(mod, "ZOMBIE_TIMEOUT_SECONDS")


# ── KanbanDB CRUD ──────────────────────────────────────────

class TestKanbanDB:
    """KanbanDB 기본 CRUD 사이클"""

    def test_init_creates_db(self, temp_db):
        """__init__ → DB 파일 생성"""
        mod = _import_kanban()
        db = mod.KanbanDB(db_path=temp_db)
        assert Path(temp_db).exists()

    def test_add_card_returns_id(self, temp_db):
        """add_card → 정수 PK 반환"""
        mod = _import_kanban()
        db = mod.KanbanDB(db_path=temp_db)
        card_id = db.add_card("Test Task")
        assert isinstance(card_id, int)
        assert card_id > 0

    def test_add_card_default_column(self, temp_db):
        """add_card 기본값 column='TODO'"""
        mod = _import_kanban()
        db = mod.KanbanDB(db_path=temp_db)
        card_id = db.add_card("Default column task")
        cards = db.list_cards()
        assert any(c["id"] == card_id for c in cards["TODO"])

    def test_add_card_custom_column(self, temp_db):
        """add_card 지정 column='IN_PROGRESS'"""
        mod = _import_kanban()
        db = mod.KanbanDB(db_path=temp_db)
        card_id = db.add_card("In progress task", column="IN_PROGRESS")
        cards = db.list_cards()
        assert any(c["id"] == card_id for c in cards["IN_PROGRESS"])

    def test_list_cards_structure(self, temp_db):
        """list_cards → {'TODO': [], 'IN_PROGRESS': [], 'DONE': []}"""
        mod = _import_kanban()
        db = mod.KanbanDB(db_path=temp_db)
        cards = db.list_cards()
        assert set(cards.keys()) == {"TODO", "IN_PROGRESS", "DONE"}
        for col in cards:
            assert isinstance(cards[col], list)

    def test_move_card(self, temp_db):
        """add_card → move_card → list_cards 변경 확인"""
        mod = _import_kanban()
        db = mod.KanbanDB(db_path=temp_db)
        card_id = db.add_card("Move test")
        assert db.move_card(card_id, "DONE") is True
        cards = db.list_cards()
        assert any(c["id"] == card_id for c in cards["DONE"])

    def test_move_card_invalid_column(self, temp_db):
        """잘못된 컬럼명 → ValueError"""
        mod = _import_kanban()
        db = mod.KanbanDB(db_path=temp_db)
        card_id = db.add_card("Invalid move")
        with pytest.raises(ValueError):
            db.move_card(card_id, "INVALID")

    def test_delete_card(self, temp_db):
        """add_card → delete_card → list_cards에서 제거"""
        mod = _import_kanban()
        db = mod.KanbanDB(db_path=temp_db)
        card_id = db.add_card("Delete me")
        assert db.delete_card(card_id) is True
        cards = db.list_cards()
        all_ids = [c["id"] for col in cards.values() for c in col]
        assert card_id not in all_ids

    def test_get_zombies(self, temp_db):
        """get_zombies — IN_PROGRESS + 오래된 heartbeat → zombie"""
        mod = _import_kanban()
        db = mod.KanbanDB(db_path=temp_db)
        z_id = db.add_card("Zombie task", column="IN_PROGRESS")
        db.claim_card(z_id, "worker1")
        # heartbeat_at을 과거로 강제 설정 (raw SQL)
        import sqlite3
        import datetime
        old_ts = (datetime.datetime.utcnow() - datetime.timedelta(seconds=3600)).isoformat()
        with sqlite3.connect(temp_db) as conn:
            conn.execute("UPDATE cards SET heartbeat_at = ? WHERE id = ?", (old_ts, z_id))
            conn.commit()
        zombies = db.get_zombies()
        assert any(z["id"] == z_id for z in zombies)

    def test_multiple_cards(self, temp_db):
        """여러 카드 추가 및 카운트"""
        mod = _import_kanban()
        db = mod.KanbanDB(db_path=temp_db)
        ids = []
        for i in range(5):
            ids.append(db.add_card(f"Task {i}"))
        cards = db.list_cards()
        total = sum(len(v) for v in cards.values())
        assert total == 5

    def test_heartbeat_updates(self, temp_db):
        """heartbeat → zombie 해제"""
        mod = _import_kanban()
        db = mod.KanbanDB(db_path=temp_db)
        cid = db.add_card("Heartbeat test", column="IN_PROGRESS")
        db.claim_card(cid, "w1")
        assert db.heartbeat(cid, "w1") is True
        zombies = db.get_zombies()
        assert not any(z["id"] == cid for z in zombies)
