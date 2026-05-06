# Clinical Guideline Corpus for RAG System

A directory of clinical guideline documents for use in a RAG (retrieval-augmented generation) system supporting clinicians (MD, NP, PA) in multi-specialty primary care.

## Corpus Composition

**47 documents** organized into 7 categories. All documents are derived from public-domain US sources:

- **U.S. Preventive Services Task Force (USPSTF)** — screening, counseling, preventive medication
- **Centers for Disease Control and Prevention (CDC)** — clinical practice guidelines, immunization schedules, infection control, antibiotic stewardship
- **National Heart, Lung, and Blood Institute (NHLBI)** — cardiovascular and lifestyle guidelines

All sources are public domain (US government work) and free for redistribution.

## Directory Structure

```
corpus/
├── preventive/           (10 docs) - General preventive services and lifestyle
├── cardiometabolic/      (8 docs)  - Hypertension, lipids, diabetes, weight, pregnancy CVD
├── cancer_screening/     (7 docs)  - Cancer screening and risk reduction
├── infectious_disease/   (10 docs) - STIs, HIV, hepatitis, TB, C. diff
├── mental_health_substance/ (8 docs) - Depression, anxiety, substance use, IPV
├── immunizations/        (2 docs)  - Adult and pediatric vaccine schedules
└── pharmacology/         (2 docs)  - Opioid prescribing, antibiotic stewardship
```

## Document Format

Each document is a markdown file with YAML front matter for metadata:

```yaml
---
source_organization: U.S. Preventive Services Task Force (USPSTF)
title: <full title>
publication_date: YYYY-MM-DD
grade: A | B | C | D | I (or applicable evidence grade)
population: <target patient population>
source_url: <official URL>
license: Public domain (US government work)
topic_tags: <comma-separated tags>
---
```

The body contains the clinical content: recommendation summary, clinician summary, implementation guidance, and supporting clinical detail.

## Suggested Use in a Basic RAG

1. **Ingestion:** Parse each `.md` file. The YAML front matter becomes structured metadata; the markdown body is the chunk-able text.
2. **Chunking:** Suggested chunking strategy: split at H2/H3 headers (`##` and `###`). Each chunk should retain its source metadata.
3. **Indexing:** Build an embedding index over the chunks. Optionally also build a structured filter on metadata fields (`grade`, `population`, `topic_tags`) for hybrid retrieval.
4. **Retrieval:** When the LLM needs to answer a clinical question, retrieve top-k chunks and pass them as context.

## Metadata Schema (for hybrid retrieval)

| Field | Description | Example |
|---|---|---|
| `source_organization` | Issuing body | "U.S. Preventive Services Task Force (USPSTF)" |
| `title` | Full document title | "Hypertension in Adults Screening" |
| `publication_date` | ISO 8601 date | "2021-04-27" |
| `grade` | Evidence grade(s) | "A", "B (40-74y), I (75+)", etc. |
| `population` | Target population | "Adults 18 years or older without known hypertension" |
| `source_url` | Original URL | <https URL> |
| `license` | License terms | "Public domain (US government work)" |
| `topic_tags` | Comma-separated topic keywords | "hypertension, blood pressure, screening" |
| `status` | (Optional) "Update in progress" if applicable | |
| `note` | (Optional) Important caveats (e.g., licensing, currency) | |

## Important Caveats

### 1. Currency
All documents include a `publication_date` in the front matter. **Stale guidelines are worse than no guidelines.** Before deploying:
- Verify each document is the current published version
- Establish a re-fetch schedule (annual is reasonable for this corpus)
- Filter retrieval to current versions; flag documents whose `status` indicates update in progress

### 2. ACIP / immunization conflicts
As of early 2026, the federal ACIP/CDC immunization recommendation process has been disrupted. The American Academy of Pediatrics, AAFP, ACOG, IDSA, and a coalition of medical societies have published independent immunization schedules that may diverge from the CDC schedule. The included CDC schedules note this. For deployment, consider also ingesting the AAP and AAFP independent schedules (copyrighted but freely available; check licensing).

### 3. What's NOT included
This corpus deliberately excludes:
- **Specialty-society guidelines (ADA, ACC/AHA, IDSA, ACP, AAFP, etc.)** — copyrighted and license-restricted for ML use; obtain permissions before adding
- **UpToDate, DynaMed, ClinicalKey** — explicit commercial license restrictions
- **Individual journal articles** — generally not "guidelines"
- **NCCN Guidelines** — registration-gated and license-restricted
- **ACOG Practice Bulletins** — member-only access for many

### 4. The example use case (overweight patient → programs)

For your stated example query ("My patient is overweight. What programs should I recommend?"), the most relevant documents are:
- `preventive/uspstf_obesity_adults_behavioral_interventions.md`
- `preventive/uspstf_prediabetes_t2dm_screening.md`
- `preventive/cdc_national_diabetes_prevention_program.md` (referral pathway)
- `cardiometabolic/nhlbi_obesity_evaluation_treatment.md` (assessment framework)
- `cardiometabolic/nhlbi_dash_eating_plan.md` (specific dietary intervention)
- `cardiometabolic/uspstf_diet_exercise_counseling_cvd_risk.md` (counseling intervention)

These cover screening, evaluation, dietary intervention, behavioral counseling, and the specific referral pathway (CDC National DPP).

## Extending the Corpus

To add documents safely:

1. **Maintain the YAML front matter schema** — this is what makes the corpus consistent.
2. **Add to the appropriate category folder** (or create a new one).
3. **Use clean markdown with section headers** (`##`, `###`) — these become natural chunk boundaries.
4. **Document licensing** in each file's `license` field.
5. **Track update cadence** — annual updates of frequently-changing sources (USPSTF, ADA, ACIP).

## Sources

All source URLs are recorded in each document's front matter. Primary sources:

- USPSTF: https://www.uspreventiveservicestaskforce.org/
- CDC: https://www.cdc.gov/
- NHLBI: https://www.nhlbi.nih.gov/
- NIH: https://www.nih.gov/

## License

The clinical content in this corpus is derived from US government public-domain sources. The compilation, formatting, and metadata schema are provided for use in your RAG system.
