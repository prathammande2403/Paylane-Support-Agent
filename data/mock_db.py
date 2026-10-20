"""
data/mock_db.py
Pre-seeded test database for Paylane support agent assessment.
Contains mock customers, transactions, support tickets, and a fixed UTC test clock.
"""

import copy
from datetime import datetime, timezone, timedelta

# Fixed UTC test clock as specified in Section 3
FIXED_TEST_CLOCK = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)

# Initial Mock Customer Records (at least 6 customers, including one frozen card)
INITIAL_CUSTOMERS = {
    "user1@paylane.com": {
        "id": "cust_101",
        "name": "Alex Morgan",
        "email": "user1@paylane.com",
        "card_last4": "4321",
        "status": "active",
        "verified": False,
    },
    "user2@paylane.com": {
        "id": "cust_102",
        "name": "Jordan Lee",
        "email": "user2@paylane.com",
        "card_last4": "8876",
        "status": "frozen",  # Frozen card scenario
        "verified": False,
    },
    "user3@paylane.com": {
        "id": "cust_103",
        "name": "Taylor Swift",
        "email": "user3@paylane.com",
        "card_last4": "1122",
        "status": "active",
        "verified": False,
    },
    "sam@paylane.com": {
        "id": "cust_104",
        "name": "Sam Rivera",
        "email": "sam@paylane.com",
        "card_last4": "5544",
        "status": "active",
        "verified": False,
    },
    "morgan@paylane.com": {
        "id": "cust_105",
        "name": "Morgan Chen",
        "email": "morgan@paylane.com",
        "card_last4": "9900",
        "status": "active",
        "verified": False,
    },
    "casey@paylane.com": {
        "id": "cust_106",
        "name": "Casey Kim",
        "email": "casey@paylane.com",
        "card_last4": "3322",
        "status": "active",
        "verified": False,
    },
}

# Initial Mock Transaction Records
INITIAL_TRANSACTIONS = {
    # 1. $40 settled 5 days old (eligible standard refund)
    "TXN_1001": {
        "txn_id": "TXN_1001",
        "customer_id": "cust_101",
        "amount": 40.00,
        "date": (FIXED_TEST_CLOCK - timedelta(days=5)).isoformat(),
        "merchant": "Coffee Shop",
        "status": "settled",
    },
    # 2. $75.00 settled 29 days old (eligible boundary refund)
    "TXN_1002": {
        "txn_id": "TXN_1002",
        "customer_id": "cust_101",
        "amount": 75.00,
        "date": (FIXED_TEST_CLOCK - timedelta(days=29)).isoformat(),
        "merchant": "Electronics Hub",
        "status": "settled",
    },
    # 3. $75.01 settled 10 days old (over $75 limit -> escalates to Billing & Refunds)
    "TXN_1003": {
        "txn_id": "TXN_1003",
        "customer_id": "cust_101",
        "amount": 75.01,
        "date": (FIXED_TEST_CLOCK - timedelta(days=10)).isoformat(),
        "merchant": "Department Store",
        "status": "settled",
    },
    # 4. $30 settled exactly 30 days old (ineligible -> escalates to Billing & Refunds)
    "TXN_1004": {
        "txn_id": "TXN_1004",
        "customer_id": "cust_101",
        "amount": 30.00,
        "date": (FIXED_TEST_CLOCK - timedelta(days=30)).isoformat(),
        "merchant": "Bookstore",
        "status": "settled",
    },
    # 5. $20 pending (ineligible -> pending)
    "TXN_1005": {
        "txn_id": "TXN_1005",
        "customer_id": "cust_101",
        "amount": 20.00,
        "date": (FIXED_TEST_CLOCK - timedelta(days=1)).isoformat(),
        "merchant": "Grocery Mart",
        "status": "pending",
    },
    # 6. $25 already refunded (ineligible -> already refunded)
    "TXN_1006": {
        "txn_id": "TXN_1006",
        "customer_id": "cust_101",
        "amount": 25.00,
        "date": (FIXED_TEST_CLOCK - timedelta(days=12)).isoformat(),
        "merchant": "Apparel Store",
        "status": "refunded",
    },
    # 7. $900-ish settled (large loss / fraud case)
    "TXN_1007": {
        "txn_id": "TXN_1007",
        "customer_id": "cust_103",
        "amount": 920.00,
        "date": (FIXED_TEST_CLOCK - timedelta(days=2)).isoformat(),
        "merchant": "Luxury Retail",
        "status": "settled",
    },
    # 8. Transaction owned by a different customer (cust_104)
    "TXN_2001": {
        "txn_id": "TXN_2001",
        "customer_id": "cust_104",
        "amount": 50.00,
        "date": (FIXED_TEST_CLOCK - timedelta(days=3)).isoformat(),
        "merchant": "Hardware Supply",
        "status": "settled",
    },
    # 9. Future-dated transaction for tests
    "TXN_FUTURE": {
        "txn_id": "TXN_FUTURE",
        "customer_id": "cust_101",
        "amount": 35.00,
        "date": (FIXED_TEST_CLOCK + timedelta(days=3)).isoformat(),
        "merchant": "Futuristic Goods",
        "status": "settled",
    },
}

def get_fresh_db() -> dict:
    """Returns a completely fresh, deep-copied in-memory DB dictionary."""
    return {
        "customers": copy.deepcopy(INITIAL_CUSTOMERS),
        "transactions": copy.deepcopy(INITIAL_TRANSACTIONS),
        "tickets": {},
        "refunds": {},
    }

# Shared working copies for backwards compatibility
MOCK_CUSTOMERS = copy.deepcopy(INITIAL_CUSTOMERS)
MOCK_TRANSACTIONS = copy.deepcopy(INITIAL_TRANSACTIONS)
MOCK_TICKETS = {}

def reset_mock_db():
    """Resets shared in-memory databases to their initial pristine state."""
    MOCK_CUSTOMERS.clear()
    MOCK_CUSTOMERS.update(copy.deepcopy(INITIAL_CUSTOMERS))
    MOCK_TRANSACTIONS.clear()
    MOCK_TRANSACTIONS.update(copy.deepcopy(INITIAL_TRANSACTIONS))
    MOCK_TICKETS.clear()