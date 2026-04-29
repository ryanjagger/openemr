# OpenEMR User Profiles for AI Agent Planning

During the audit, we identified several user types — for example, physicians, clinicians, front office users, and admin users. The actual authorization comes from ACL group membership and the permissions assigned to those groups.
OpenEMR’s installation docs mention that a user may belong to multiple groups, and that setup configures phpGACL access controls and grants the initial user administrator access:

User account
  -> belongs to one or more groups / roles
  -> groups have ACL permissions
  -> code checks ACLs before showing or performing actions
  -> patient context / encounter context further constrains what the user can access
  -> API/FHIR scopes add another permission layer for API access


From default user types, I had Opus 4.7 develop six profiles, one per role, each with a typical use case they might encounter. Keep in mind that these are not user stories, but user stories could be derived from these personas in the future.

---

## 1. Admin → "Monday-morning practice manager"

**Persona:** Practice manager at a 4-physician primary care clinic. Non-clinical, owns operations. Knows OpenEMR's admin surface but not its plumbing. Pre-agent reality: 7:30am Monday, 30 minutes before the staff huddle.

**Pre-agent moment:** They've just sat down with coffee. They want to walk into huddle knowing three things: what broke over the weekend, who needs an account adjustment today, and whether any audit-log entries from Friday look weird (e.g., a billing user reading clinical notes at 11pm).

**Use case (8:00–8:30am):** "What's changed in the system since Friday at 5pm — failed logins, locked accounts, ACL changes, unusual access patterns, scheduled jobs that didn't run? And the new MA Rachel starts at 9 — does she have an account yet, and is it provisioned with the right ACLs to match Jordan's?"

**Why conversational:** The questions are unpredictable and cross-cut tables (`users`, `log`, `gacl_*`, `facility`, scheduled jobs). A dashboard requires deciding the questions in advance. The practice manager doesn't know what's wrong until something looks off — and then they need to drill in ("why did Maria fail login 8 times?") without writing SQL or digging through the audit viewer.

---

## 2. Physician → "20-slot primary care, between patients"

**Persona:** PCP, 15-minute slots, 20 patients/day, finishes notes at 9pm. Not the doc with a scribe — the one absorbing the chart-prep cost themselves. Tolerance for hallucination: near zero on meds and labs; higher on summarization of past notes.

**Pre-agent moment:** 8:52am. First patient is rooming. They have ~60 seconds before walking in. They have not pre-charted. They need: what's changed since I last saw this patient, what did I tell them last time, what's their agenda today.

**Use case (the 60-second brief):** "Give me a 5-line brief on Mrs. Garcia: what's new since last visit, what's overdue, what she wrote on the pre-visit form, and one thing I should not forget to mention." Output is read in 15 seconds. Then 1–2 drill-down questions while walking down the hall: "What did the ED give her three weeks ago?" "Has she filled the metformin?"

**Why conversational:** The brief is the wedge — the value is in the follow-ups. The doc doesn't know which patient will need the drill-down until they read the brief. A static dashboard forces them to scan all 20 patients at the same depth; the agent lets them go shallow on 17 and deep on 3. Voice-capable in the hallway is a real differentiator.

**The trap to avoid:** This must not become a "summarize the chart" feature. The bound is tight: 5 lines, written for _this_ doc's _next 15 minutes_, optimized for the chief complaint they're about to hear.

---

## 3. Clinician → "MA doing rooming with a messy med list"

**Persona:** Medical assistant, 7 minutes per room, does vitals + med rec + screening prompts before the doc walks in. Patient population is older, polypharmacy, multiple outside prescribers.

**Pre-agent moment:** 9:15am. Patient is on the table. MA opens the chart, sees a med list from 4 months ago. Patient says: "I'm not taking the water pill anymore, the kidney doctor stopped it, and I'm on the new blue one twice a day."

**Use case (med reconciliation in 90 seconds):** Agent ingests the patient's verbal report + any outside e-prescribing data + recent encounter notes from other systems and proposes: "Stop furosemide (per nephrology 2026-02-14, per outside note). Add: likely amlodipine 5mg BID — confirm with patient (she said 'blue, twice a day,' matches her cardiology refill 2026-03-02)." MA confirms or corrects, agent updates med list.

**Why conversational:** Patient speech is messy and ambiguous ("the small white one for my sugar"). The MA is translating in real time. A form-based med rec UI requires the MA to already know the answer. The agent's job is to propose, justify ("here's why I think it's amlodipine, not lisinopril"), and let the MA say "no, it's actually her atorvastatin" — and re-propose.

---

## 4. Front Office → "Phone-and-counter juggle at 9:30am"

**Persona:** Front desk at a 4-doc practice. Knows which docs cover for whom, which complaints the NP can handle, who tolerates being double-booked. This knowledge is in their head, not the system.

**Pre-agent moment:** Phone is ringing. Mrs. Rodriguez is at the counter, her insurance card doesn't match what's on file, eligibility check just failed. The caller wants the soonest appointment because their kid has a sore throat and a fever.

**Use case (smart scheduling triage):** Agent on phone-side: "Dr. Smith is booked 6 weeks. For a same-day sick visit on a pediatric patient, the practice pattern is to offer NP Lee — she has 11:20 and 2:45 today. Want me to hold 11:20 and send the intake link?" Simultaneously on counter-side: "Mrs. Rodriguez's plan changed from BCBS PPO to BCBS HMO 2026-04-01 — same payer, new member ID format. Want me to update and re-run eligibility?"

**Why conversational:** Scheduling is full of soft rules ("Dr. Smith doesn't double-book Mondays, but Dr. Lee will if it's an established patient"). These rules live in tribal knowledge. A scheduling search UI can encode hard constraints (provider, time, duration) but not the practice's judgment. The front office wants to ask _"is there anything sooner if we shorten to 15 min or move her to telehealth?"_ — that's a conversation, not a filter.

---

## 5. Accounting → "Biller on Monday AR triage"

**Persona:** Single biller for a 4-doc practice. Owns AR. Monday morning ritual: open aging report, decide what to chase first. Knows the payer-specific quirks (Aetna denies for missing modifier 25 a lot; UHC sits on claims until you call).

**Pre-agent moment:** 47 claims over 60 days, $84K outstanding. They have ~3 hours before the afternoon's incoming claims start. They need to triage by _recoverable dollars per minute of effort_.

**Use case (denial clustering + draft response):** Agent: "Of the 47: 12 are missing modifier 25 — I can re-bill all of them in one batch (est. $9.4K). 8 are prior-auth denials but you have the auth on file from the patient portal, never attached — I drafted appeal letters with the auth attached. 5 are eligibility denials from a payer change mid-cycle — patient owes, want me to draft statements? 22 are just sitting at UHC — call list with member IDs." Biller approves in batches.

**Why conversational:** Each denial has a story. The biller wants to ask "show me the EOB and the original claim side by side for this one" or "did we have prior auth for this patient last year?" then act in bulk. A static denial-management report can list problems; only a conversation can negotiate the _next action_ per cluster.

---

## 6. Emergency → "ED resident, 2am intake, altered MS"

**Persona:** Second-year EM resident on overnight. Tolerance for clicking through tabs at 2am: zero. Tolerance for hallucination on code status, allergies, or last imaging: zero.

**Pre-agent moment:** Triage nurse hands off: "78F, BIBA, family says she's confused since dinner." Resident has 60 seconds before walking into the room. The chart has 11 prior encounters across this system plus outside records.

**Use case (60-second intake synthesis):** Agent produces — voice-readable while walking — "78F, DM2, CHF, CKD3. Last admission 3 weeks ago for CHF exacerbation, discharged on Lasix 80 BID. **DNR/DNI**. PCN allergy. Baseline A&Ox3 per PCP note 8 weeks ago. No imaging since 2026-01. Last BMP creatinine 1.8." Then in-room: "What was her last potassium?" "When did she last see neuro?" — voice answers, hands stay on the patient.

**Why conversational:** Speed and hands-free matter most here. The resident's questions are emergent and follow what they see at bedside ("she has a UTI smell — last UA?"). A dashboard requires choosing the questions in advance; this resident doesn't know what they need until they're standing over the patient. Code status and allergies are the high-stakes facts — the agent must surface them every time, unprompted, in a fixed slot of the brief.

---

## A note on the next step

Each of these is a hypothesis, not a validated user. Before building, shadow one of each role at a real OpenEMR-using practice for a half-day and time-stamp the pre-agent moment to confirm the constraint (the "60 seconds" or "7 minutes" bound) is actually where the pain lives. The personas that hold up under shadowing become the first agents; the ones that don't get rewritten or dropped.
