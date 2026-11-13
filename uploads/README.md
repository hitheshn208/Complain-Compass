# Demo upload files

Upload these in the app at http://localhost:5173/upload, in folder order.

| Step | Upload tab | Files |
|---|---|---|
| 1 | CSV | `1_complaints_csv/sample_complaints.csv` (~620 complaints) |
| 2 | *(no UI tab, use the terminal)* | `curl -F file=@uploads/2_population_optional/sample_population.csv http://localhost:8000/api/v1/analytics/populations`: optional, makes segment views show rates instead of counts |
| 3 | Customer directory | `3_customer_directory/customer_directory.csv`: links email/phone/social senders to existing customers and adds their country, state, age group, student status and income bracket (like a CRM export) |
| 4 | Email | everything in `4_email/` (`.eml` files + `inbox_export.mbox`) |
| 5 | Chat | `5_chat/chat_transcripts.json` |
| 6 | Social media | `6_social_media/social_posts.jsonl` |
| 7 | Voice calls | all `.wav` files **plus** `voice_manifest.csv` from `7_voice_calls/` |

What to point out:
- 20 of the email/chat/social/voice items re-report problems already in the CSV (same customer, same order).
  After steps 3–7 the report counts go up, but those 20 don't create new incidents. That shows cross-channel
  dedup working.
- **Best single demo: Vik (CUST-1060), order ORD-396765, a delivery stuck "in transit".** He reports it in the CSV,
  then chases it by email (`4_email/followup_07.eml`), on Twitter (`6_social_media`, post `SM-X01`, handle
  `@vik_1060` only) and in chat (`5_chat`, `CHAT-X01`, phone number only, typing "order number 396765" without
  the `ORD-` prefix). All of it lands in **one incident**: several reports across 4 channels, counted as 1 affected customer.
  Search the Complaints page for `396765` to show it.
- Same channel, same customer, same problem: in `5_chat/chat_transcripts.json`, `CHAT-R01` and `CHAT-R02` are
  second conversations about earlier chats (`CHAT-N01`, `CHAT-N03`), worded differently. Each merges into the
  original's incident: R01 via the same order number (the customer is only known by email, linked through the
  directory), R02 with no order number at all (same customer + within 7 days + similar meaning + same topic).
- `4_email/auto_reply.eml` is rejected on purpose (auto-replies are filtered).
- Uploading the same file twice is rejected (`409`).
