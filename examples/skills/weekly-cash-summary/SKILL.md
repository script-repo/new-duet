---
name: weekly-cash-summary
description: Build a weekly cash summary (sheet + short note) from payments data and share it with the bookkeeper.
---

# Weekly cash summary

1. Read this week's payments (`payments_list`, or the payments connector you have).
2. Save the raw rows to `data/payments.csv` in your workspace.
3. Create an `xlsx` artifact with two sheets: **Summary** (count, total, items needing attention) and
   **Detail** (date, customer, amount, status, with a `=SUM()` total row).
4. Write a 3-bullet note: total in, anything failed or overdue, one recommended action.
5. If asked to share it, email the note to the bookkeeper with the file name mentioned. The send needs approval.
