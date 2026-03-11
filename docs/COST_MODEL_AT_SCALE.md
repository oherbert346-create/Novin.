# Novin Home — Cost Model at Scale

Cost projection including API usage, deployment, database, and handling at mass scale.

## 1. Per-Frame Token Usage (from telemetry, Groq)

| Component | Input tokens | Output tokens | Total |
|-----------|--------------|---------------|-------|
| Vision (Llama 4 Scout 17B) | ~1,384 | ~140 | ~1,524 |
| Reasoning (Llama 3.1 8B × 4 agents) | ~5,520 | ~566 | ~6,086 |
| Reasoning (GPT-OSS 120B × 4 agents) | ~5,740 | ~797 | ~6,537 |
| **Total per frame (8B)** | **~6,904** | **~706** | **~7,610** |
| **Total per frame (120B)** | **~7,124** | **~937** | **~8,061** |

## 2. Groq API Pricing (per million tokens)

| Model | Input | Output |
|-------|-------|--------|
| Llama 4 Scout 17B (vision) | $0.11 | $0.34 |
| Llama 3.1 8B Instant (reasoning) | $0.05 | $0.08 |
| GPT-OSS 120B (reasoning) | $0.15 | $0.60 |

## 3. Per-Frame API Cost

**Llama 3.1 8B (faster, cheaper):**
```
Vision:   (1384 × $0.11 + 141 × $0.34) / 1e6 = $0.000191
Reasoning: (5520 × $0.05 + 566 × $0.08) / 1e6 = $0.000314
────────────────────────────────────────────────────────
Per frame: $0.000505 (~0.05¢)
```

**GPT-OSS 120B (better quality, higher cost):**
```
Vision:   (1384 × $0.11 + 139 × $0.34) / 1e6 = $0.000191
Reasoning: (5740 × $0.15 + 797 × $0.60) / 1e6 = $0.001339
────────────────────────────────────────────────────────
Per frame: $0.00153 (~0.15¢)
```

**Latency (measured via `scripts/benchmark_pipeline.py`):**
| Model | Mean E2E | p50 | Reasoning phase1+2 |
|-------|----------|-----|--------------------|
| Llama 3.1 8B | ~1,866 ms | ~1,579 ms | ~800 ms |
| GPT-OSS 120B | ~2,089 ms | ~2,223 ms | ~1,250 ms |

## 4. Scale Assumptions

| Tier | Homes | Cameras | Events/day | Events/month |
|------|-------|---------|------------|--------------|
| Pilot | 10 | 20 | 500 | 15,000 |
| Growth | 100 | 250 | 5,000 | 150,000 |
| Scale | 1,000 | 2,500 | 50,000 | 1,500,000 |
| Mass | 10,000 | 25,000 | 500,000 | 15,000,000 |

*Assumption: ~20 events/day per camera (motion-triggered ingest).*

## 5. API Cost by Tier

| Tier | Frames/month | API cost (8B) | API cost (120B) |
|------|--------------|---------------|-----------------|
| Pilot | 15,000 | $7.82 | $23.09 |
| Growth | 150,000 | $78.17 | $231 |
| Scale | 1,500,000 | $782 | $2,309 |
| Mass | 15,000,000 | $7,817 | $23,086 |

## 6. Deployment & Infrastructure Costs

### 6.1 Compute (app servers)

| Tier | Instances | Spec | Cost/month |
|------|-----------|------|------------|
| Pilot | 1 | t2.small (1 vCPU, 2GB) | $17 |
| Growth | 2 | t2.small × 2 | $34 |
| Scale | 5 | t2.medium or t3.small | $85–120 |
| Mass | 20+ | Auto-scaling group | $400–600 |

*AWS EC2 t2.small ≈ $0.023/hr ≈ $17/mo.*

### 6.2 Database (24/7)

| Tier | Option | Spec | Cost/month |
|------|--------|------|------------|
| Pilot | Supabase Pro / RDS t4g.micro | 1–2GB RAM | $12–25 |
| Growth | RDS t4g.small | 2 vCPU, 4GB | $50 |
| Scale | RDS m5.large | 2 vCPU, 8GB | $130 |
| Mass | RDS multi-AZ / Aurora | 16GB+ | $400–800 |

### 6.3 Storage (events, thumbnails, logs)

| Tier | Storage | Cost (S3/block) |
|------|---------|-----------------|
| Pilot | 10 GB | ~$0.25 |
| Growth | 100 GB | ~$2.50 |
| Scale | 1 TB | ~$25 |
| Mass | 10 TB | ~$250 |

### 6.4 Handling (ingest, queues, workers)

- **Ingest API**: Same compute as app; negligible extra.
- **Async workers**: +1 small instance at scale = +$17/mo.
- **Load balancer**: ~$20/mo (ALB).
- **CDN / egress**: ~$0.09/GB beyond free tier; ~$50–200/mo at mass scale.

## 7. Total Cost Summary (monthly)

**With Llama 3.1 8B reasoning:**
| Tier | API | Compute | DB | Storage | Other | **Total** |
|------|-----|---------|-----|---------|-------|-----------|
| Pilot | $8 | $17 | $25 | $0.25 | $0 | **~$50** |
| Growth | $76 | $34 | $50 | $2.50 | $20 | **~$182** |
| Scale | $758 | $120 | $130 | $25 | $50 | **~$1,083** |
| Mass | $7,575 | $500 | $600 | $250 | $200 | **~$9,125** |

**With GPT-OSS 120B reasoning:**
| Tier | API | Compute | DB | Storage | Other | **Total** |
|------|-----|---------|-----|---------|-------|-----------|
| Pilot | $23 | $17 | $25 | $0.25 | $0 | **~$65** |
| Growth | $231 | $34 | $50 | $2.50 | $20 | **~$338** |
| Scale | $2,309 | $120 | $130 | $25 | $50 | **~$2,634** |
| Mass | $23,086 | $500 | $600 | $250 | $200 | **~$24,636** |

## 8. Cost per Home per Month

| Tier | Total (8B) | Total (120B) | Homes | **Per home (8B)** | **Per home (120B)** |
|------|------------|--------------|-------|-------------------|---------------------|
| Pilot | $50 | $65 | 10 | **$5.00** | **$6.50** |
| Growth | $182 | $338 | 100 | **$1.82** | **$3.38** |
| Scale | $1,083 | $2,634 | 1,000 | **$1.08** | **$2.63** |
| Mass | $9,125 | $24,636 | 10,000 | **$0.91** | **$2.46** |

## 9. Cost per Event

| Tier | Total (8B) | Total (120B) | Events/month | **Per event (8B)** | **Per event (120B)** |
|------|------------|--------------|--------------|--------------------|----------------------|
| Pilot | $50 | $65 | 15,000 | **$0.0033** | **$0.0043** |
| Growth | $182 | $338 | 150,000 | **$0.0012** | **$0.0023** |
| Scale | $1,083 | $2,634 | 1,500,000 | **$0.00072** | **$0.0018** |
| Mass | $9,125 | $24,636 | 15,000,000 | **$0.00061** | **$0.0016** |

## 10. Cost Drivers

1. **API (Groq)** — Dominant at scale; ~83% at Mass tier (8B); ~92% at Mass tier (120B).
2. **Database** — Fixed 24/7 cost; ~6–7% at scale.
3. **Compute** — Grows with concurrency; ~5% at scale.
4. **Storage** — Grows with retention; ~3% at mass.

## 11. Optimisation Levers

- **Vision-on-demand**: Skip vision for low-priority frames; could cut API cost 30–50%.
- **Batch API**: Groq Batch is 50% cheaper; suitable for non-real-time flows.
- **Reserved instances**: 1–3 year commits cut compute/DB 30–50%.
- **Token reduction**: Shorter prompts, smaller models; ~10–20% savings.
- **Caching**: Prompt caching (Groq) reduces input cost when context repeats.
