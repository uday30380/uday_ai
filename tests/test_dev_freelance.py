import pytest

from app.services import developer_service as dev, freelance_service as fl
from app.utils.dates import utcnow
from app.utils.validation import amount_minor
from datetime import timedelta
from zoneinfo import ZoneInfo


def test_snippet_store_retrieve_isolation_delete(db, owner, other):
    with db.session_scope() as s:
        sn = dev.save_snippet(s, owner.id, "Hello World", "python", "demo", "print('hi')")
        sid = sn.id
        assert dev.get_snippet(s, owner.id, "hello world").code == "print('hi')"
        assert dev.get_snippet(s, other.id, "hello-world") is None
        with pytest.raises(ValueError):
            dev.save_snippet(s, owner.id, "hello-world", "python", "", "x")
        with pytest.raises(LookupError):
            dev.delete_snippet(s, other.id, sid)
        dev.delete_snippet(s, owner.id, sid)
        assert dev.list_snippets(s, owner.id) == []


def test_snippet_code_is_stored_not_executed(db, owner, tmp_path):
    marker = tmp_path / "pwned"
    with db.session_scope() as s:
        dev.save_snippet(s, owner.id, "evil", "python", "", f"open(r'{marker}','w').write('x')")
    assert not marker.exists()


def test_projects_and_worklog(db, owner, other):
    with db.session_scope() as s:
        dev.create_project(s, owner.id, "Portfolio", "React", "https://github.com/u/p")
        dev.log_work(s, owner.id, "portfolio", "Built navbar")
        assert dev.set_status(s, owner.id, "Portfolio", "paused").status == "paused"
        with pytest.raises(LookupError):
            dev.log_work(s, other.id, "Portfolio", "intrusion")
        with pytest.raises(ValueError):
            dev.create_project(s, owner.id, "Bad", repo_url="javascript:alert(1)")
        with pytest.raises(ValueError):
            dev.set_status(s, owner.id, "Portfolio", "weird")
    with db.session_scope() as s:
        assert len(dev.get_project(s, owner.id, "portfolio").activity) == 2


def test_builtin_help_topics():
    assert "git switch" in dev.builtin_help("how do I make a git branch")
    assert "SELECT" in dev.builtin_help("sql join help")
    assert "built-in topic" in dev.builtin_help("quantum gravity")


@pytest.mark.parametrize("text,minor", [("1500", 150000), ("₹1,500.50", 150050), ("0.01", 1), ("2000 INR", 200000)])
def test_amount_parsing(text, minor):
    assert amount_minor(text) == minor


@pytest.mark.parametrize("bad", ["abc", "-5", "0", "nan", "inf", ""])
def test_bad_amounts(bad):
    with pytest.raises(ValueError):
        amount_minor(bad)


def _project(s, owner, amount=1000000):
    c = fl.add_client(s, owner.id, "Acme")
    return fl.create_project(s, owner.id, c, "Website", "desc", amount)


def test_partial_payments_balances_and_history(db, owner):
    with db.session_scope() as s:
        p = _project(s, owner)             # ₹10,000.00
        fl.record_payment(s, owner.id, p.id, 300000, "UPI")
        fl.record_payment(s, owner.id, p.id, 200000)
        assert fl.paid_minor(s, p.id) == 500000 and fl.outstanding_minor(s, p) == 500000
        with pytest.raises(ValueError):
            fl.record_payment(s, owner.id, p.id, 600000)     # more than outstanding
        sm = fl.summary(s, owner.id)
        assert sm["revenue"] == {"INR": 500000} and sm["outstanding"] == {"INR": 500000}


def test_reversal_keeps_history(db, owner):
    with db.session_scope() as s:
        p = _project(s, owner)
        pay = fl.record_payment(s, owner.id, p.id, 400000)
        fl.reverse_payment(s, owner.id, pay.id)
        with pytest.raises(ValueError):
            fl.reverse_payment(s, owner.id, pay.id)          # only once
        hist = fl.payment_history(s, owner.id, p.id)
        assert [h.amount_minor for h in hist] == [400000, -400000]
        assert fl.paid_minor(s, p.id) == 0


def test_financial_isolation_between_users(db, owner, other):
    with db.session_scope() as s:
        p = _project(s, owner)
        pid = p.id
        assert fl.get_project(s, other.id, pid) is None
        with pytest.raises(LookupError):
            fl.record_payment(s, other.id, pid, 100)
        assert fl.get_client(s, other.id, "Acme") is None
        c = fl.get_client(s, owner.id, "Acme")
        with pytest.raises(PermissionError):
            fl.create_project(s, other.id, c, "steal", "", 1)


def test_multi_currency_summary_and_cancelled_projects(db, owner):
    with db.session_scope() as s:
        c = fl.add_client(s, owner.id, "Globex")
        a = fl.create_project(s, owner.id, c, "A", "", 100000, "INR")
        b = fl.create_project(s, owner.id, c, "B", "", 5000, "USD")
        fl.set_project_status(s, owner.id, a.id, "cancelled")
        sm = fl.summary(s, owner.id)
        assert sm["outstanding"] == {"USD": 5000} or sm["outstanding"].get("INR", 0) == 0


def test_followup_creates_reminder_and_overdue_detection(db, owner):
    with db.session_scope() as s:
        c = fl.add_client(s, owner.id, "Acme")
        f = fl.add_followup(s, owner.id, c, utcnow() + timedelta(days=1), "invoice status")
        assert f.reminder_id
        assert fl.overdue_followups(s, owner.id) == []
        assert len(fl.overdue_followups(s, owner.id, now=utcnow() + timedelta(days=2))) == 1


def test_invoice_pdf_and_preview(db, owner):
    with db.session_scope() as s:
        p = _project(s, owner)
        fl.record_payment(s, owner.id, p.id, 250000)
        tz = ZoneInfo("Asia/Kolkata")
        pdf = fl.invoice_pdf(s, p, tz)
        assert pdf.startswith(b"%PDF") and len(pdf) > 1000
        assert "Balance due: ₹7,500.00" in fl.invoice_preview(s, p, tz)
