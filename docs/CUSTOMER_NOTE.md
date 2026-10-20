# Draft Email to Head of Support

**Subject:** Paylane AI Support Agent – Readiness, Guardrails & Pre-Launch Questions  
**To:** Head of Customer Support, Paylane  
**From:** Antigravity / AI Engineering Team  
*(Draft for Review & Customization)*

---

Hi [Name],

I’ve finalized the initial deployment build for Paylane’s automated support agent across web chat, refund processing, and escalation workflows. Ahead of release, here is a concise summary of what the agent handles today and what we need before launching live to customers.

### What the Agent Does
- **Answers General Questions:** Clarifies Paylane refund rules, platform FAQs, and operational guidelines without requiring customer login.
- **Strict Two-Factor Verification:** Requires both the email on file and the card’s last four digits before discussing any account or balance details.
- **Automated Policy Refunds:** Safely processes refunds up to $75.00 for settled charges under 30 days old.
- **Deterministic Risk Routing:** Automatically transfers fraud reports, suspected unauthorized charges, legal inquiries, account deletions, and repeated credential failures to appropriate human teams.

### What the Agent Will NOT Do
- **Never Guesses or Exposes Data:** Never reveals stored emails, full card numbers, or confirms whether an unknown email exists.
- **No Partial or Over-Limit Refunds:** Refuses partial amounts, split claims, charges over $75, or transactions older than 30 days.
- **Never Hallucinates Freeze Reasons:** Informs customers that a card is frozen, but explicitly declines to invent reasons or attempt unfreezing.
- **Never Promises Timing:** Refrains from making SLA commitments (e.g., "refund in 2 hours") we cannot guarantee.

### What We Need Before Launch
1. **Frozen Card Reason API:** A secure read-only endpoint so customers can see why their card was locked.
2. **Standardized SLAs:** Clear human turnaround commitments so we can set proper customer expectations during handoffs.
3. **Pending Charges Protocol:** Guidance on whether pending charges can be voided or if customers must wait for settlement.
4. **Fraud Escalation Channel:** Pager/Slack integration or high-priority queue details for unauthorized losses over $500.
5. **Team Operating Hours:** Clear operating schedules for Billing, Security, Legal, and Operations teams.

Looking forward to your feedback so we can tune the final cutover!

Best regards,  
[Your Name / Engineering Team]
