"""Strategy-specific paper books (FE-0028): separate cash, reservations and fills per book; nothing is pooled implicitly."""
from __future__ import annotations

from decimal import Decimal

from financial_brain.kernel.oms import Oms, OmsError


class BookError(ValueError):
    pass


class BookSet:
    def __init__(self, capital: dict[str, Decimal]) -> None:
        if not capital or any(c <= 0 for c in capital.values()):
            raise BookError("every book needs positive capital")
        self._books = {name: Oms({name: cap}) for name, cap in capital.items()}

    def oms(self, book: str) -> Oms:
        return self._books[book]

    def transfer(self, *_args, **_kwargs) -> None:
        raise BookError("capital is never moved between books implicitly; re-allocate through a new frozen allocation")

    def submit(self, book: str, **kwargs):
        if kwargs.get("account", book) != book:
            raise OmsError(f"order for {kwargs['account']} cannot be placed in book {book}")
        return self._books[book].submit_intent(account=book, **kwargs)

    def comparison(self) -> dict[str, dict[str, Decimal]]:
        """Aggregate view: each book's own cash and reservations side by side, plus totals."""
        rows = {n: {"cash": o.cash(n), "reserved": o.reserved(n), "available": o.available(n)} for n, o in self._books.items()}
        rows["TOTAL"] = {k: sum((r[k] for r in rows.values()), Decimal(0)) for k in ("cash", "reserved", "available")}
        return rows
