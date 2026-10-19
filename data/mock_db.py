"""
data/mock_db.py
Pre-seeded test database for Paylane support agent assessment.
Contains mock customers, transactions, support tickets, and a fixed UTC test clock.
"""

import copy
from datetime import datetime, timezone, timedelta

# Fixed UTC test clock for deterministic 30-day edge-case calculations
FIXED_TEST_CLOCK = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)

# Initial Mock Customer Records
INITIAL_CUSTOMERS = {
    "user1@paylane.com": {
        "id": "cust_101",
        "name": "Alex Morgan",
        "email": "user1@paylane.com",
        "card_last4": "4321",
        "status": "active",
        "verified": False
    },
    "user2@paylane.com": {
        "id": "cust_102",
        "name": "Jordan Lee",
        "email": "user2@paylane.com",
        "card_last4": "8876",
        "status": "frozen",  # Frozen card scenario
        "verified": False
    },
    "user3@paylane.com": {
        "id": "cust_103",
        "name": "Taylor Swift",
        "email": "user3@paylane.com",
        "card_last4": "1122",
        "status": "active",
        "verified": False
    }
}

# Initial Mock Transaction Records
INITIAL_TRANSACTIONS = {
    # Eligible standard refund (< $75, < 30 days old, settled)
    "TXN_1001": {
        "txn_id": "TXN_1001",
        "customer_id": "cust_101",
        "amount": 45.50,
        "date": (FIXED_TEST_CLOCK - timedelta(days=10)).isoformat(),
        "merchant": "Coffee Shop",
        "status": "settled"
    },
    # Ineligible refund: Exceeds $75 ($120.00)
    "TXN_1002": {
        "txn_id": "TXN_1002",
        "customer_id": "cust_101",
        "amount": 120.00,
        "date": (FIXED_TEST_CLOCK - timedelta(days=5)).isoformat(),
        "merchant": "Tech Store",
        "status": "settled"
    },
    # Ineligible refund: Exactly 30 days old (Escalates per policy)
    "TXN_1003": {
        "txn_id": "TXN_1003",
        "customer_id": "cust_101",
        "amount": 30.00,
        "date": (FIXED_TEST_CLOCK - timedelta(days=30)).isoformat(),
        "merchant": "Bookstore",
        "status": "settled"
    },
    # Ineligible refund: Pending status
    "TXN_1004": {
        "txn_id": "TXN_1004",
        "customer_id": "cust_101",
        "amount": 25.00,
        "date": (FIXED_TEST_CLOCK - timedelta(days=1)).isoformat(),
        "merchant": "Grocery Mart",
        "status": "pending"
    },
    # Ineligible refund: Already refunded
    "TXN_1005": {
        "txn_id": "TXN_1005",
        "customer_id": "cust_101",
        "amount": 50.00,
        "date": (FIXED_TEST_CLOCK - timedelta(days=12)).isoformat(),
        "merchant": "Apparel Store",
        "status": "refunded"
    },
    # Fraud / Large loss transaction (> $500)
    "TXN_1006": {
        "txn_id": "TXN_1006",
        "customer_id": "cust_103",
        "amount": 650.00,
        "date": (FIXED_TEST_CLOCK - timedelta(days=2)).isoformat(),
        "merchant": "Luxury Retail",
        "status": "settled"
    }
}

# Working copies
MOCK_CUSTOMERS = copy.deepcopy(INITIAL_CUSTOMERS)
MOCK_TRANSACTIONS = copy.deepcopy(INITIAL_TRANSACTIONS)
MOCK_TICKETS = {}

def reset_mock_db():
    """Resets in-memory databases to their initial pristine state."""
    MOCK_CUSTOMERS.clear()
    MOCK_CUSTOMERS.update(copy.deepcopy(INITIAL_CUSTOMERS))
    MOCK_TRANSACTIONS.clear()
    MOCK_TRANSACTIONS.update(copy.deepcopy(INITIAL_TRANSACTIONS))
    MOCK_TICKETS.clear()