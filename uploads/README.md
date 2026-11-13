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
- `4_email/auto_reply.eml` is rejected on purpose (auto-replies are filtered).
- Uploading the same file twice is rejected (`409`).
