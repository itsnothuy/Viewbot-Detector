# Viewbot Guard - System Analysis

## 1. System Overview

The Viewbot Guard system is a real-time fraud detection platform designed to identify and mitigate fake viewers on livestreaming platforms. It processes multiple data streams to detect anomalous viewing patterns and enforce progressive countermeasures.

### 1.1 System Boundaries
- **In Scope**: HLS/LL-HLS streams, CDN logs, player analytics, bot challenges, real-time scoring
- **Out Scope**: Content moderation, payment processing, user management, mobile app analytics

### 1.2 Key Stakeholders
- **Primary Users**: Trust & Safety teams, Content moderators, Platform operators
- **Secondary Users**: Creators, Data analysts, Security teams
- **External Systems**: CDN providers, Challenge services, Monitoring platforms

## 2. Data Flow Analysis

### 2.1 High-Level Data Flow

```
[CDN Logs] ──┐
              │
[Player Events] ──┤
                  ├──► [Stream Processor] ──► [Risk Scorer] ──► [Enforcement Engine]
[Chat Events] ────┤                                              │
                  │                                              ▼
[Challenge Results] ──┘                                    [Actions & Alerts]
                                                                │
                                                                ▼
                                                          [Dashboards & APIs]
```

### 2.2 Detailed Data Processing Pipeline

#### **Stage 1: Data Ingestion**
```
External Sources ──► Kafka Topics ──► Schema Validation ──► Enrichment ──► Stream Processing

CDN (CloudFront/Fastly) ──► cdn-logs
HLS Player (hls.js) ──► player-events  
IRC/EventSub ──► chat-events
Turnstile/WAF ──► edge-challenges
```

#### **Stage 2: Real-time Processing**
```
Raw Events ──► Sessionization ──► Feature Extraction ──► Windowed Aggregation ──► Risk Scoring

Session Key: coalesce(cmcd.sid, cookie_id, ip_hash + ua_hash + ja4)
Time Windows: 30s (immediate), 5min (short-term), 1hr (long-term)
```

#### **Stage 3: Decision & Enforcement**
```
Risk Score ──► Threshold Comparison ──► Action Selection ──► Execution ──► Audit Logging

Thresholds:
- 0.0-0.3: Count normally
- 0.3-0.5: Suppress from viewer count  
- 0.5-0.8: Issue challenge (Turnstile)
- 0.8-1.0: Block + manual review
```

### 2.3 Data Entities & Relationships

#### **Core Entities**
- **Session**: Unique viewing instance with behavior tracking
- **Channel**: Stream being watched with associated metrics  
- **Decision**: Risk assessment with rationale and actions
- **Challenge**: Bot detection test with pass/fail outcome

#### **Entity Relationships**
```
Channel (1) ──► (N) Session ──► (N) Events
Session (1) ──► (N) Decision ──► (1) Action
Session (1) ──► (0..N) Challenge
```

## 3. System Architecture Analysis

### 3.1 Component Architecture

#### **Presentation Layer**
- **Grafana Dashboards**: Real-time monitoring and alerting
- **REST API**: External system integration
- **Admin UI**: Manual review and override interface

#### **Application Layer**  
- **Risk Scoring Engine**: ML models and rule evaluation
- **Session Manager**: Viewer state tracking and correlation
- **Enforcement Engine**: Action execution and workflow

#### **Data Processing Layer**
- **Flink Streaming Jobs**: Real-time event processing
- **Feature Store**: Pre-computed behavioral features
- **Model Serving**: Inference and prediction pipeline

#### **Data Storage Layer**
- **ClickHouse**: Time-series analytics and aggregations
- **Kafka**: Event streaming and message durability
- **Redis**: Session state and real-time caching

#### **Infrastructure Layer**
- **Docker Compose**: Local development orchestration
- **Kubernetes**: Production container orchestration  
- **Prometheus/Grafana**: System monitoring and alerting

### 3.2 Technology Stack Mapping

| Layer | Component | Technology | Purpose |
|-------|-----------|------------|---------|
| **Ingestion** | Message Broker | Redpanda (Kafka API) | Event streaming, durability |
| **Processing** | Stream Engine | Apache Flink (PyFlink) | Real-time computation, state management |
| **Storage** | OLAP Database | ClickHouse | Time-series analytics, fast aggregations |
| **Serving** | Cache Layer | Redis | Session state, real-time lookups |
| **Visualization** | Dashboards | Grafana | Monitoring, alerting, investigation |
| **Security** | Bot Protection | Cloudflare Turnstile | Challenge service, progressive friction |

### 3.3 Deployment Architecture

#### **Development Environment**
```
Docker Compose
├── redpanda (Kafka API)
├── clickhouse (Analytics DB)
├── flink-jobmanager 
├── flink-taskmanager
├── grafana (Dashboards)
├── nginx (Player hosting)
└── challenges (Turnstile service)
```

#### **Production Environment**
```
Kubernetes Cluster
├── Ingestion Tier
│   ├── Kafka Brokers (3x)
│   └── Schema Registry
├── Processing Tier  
│   ├── Flink JobManager
│   └── Flink TaskManagers (4x)
├── Storage Tier
│   ├── ClickHouse Cluster (3x)
│   └── Redis Cluster (3x)
└── API/UI Tier
    ├── REST API Gateway
    ├── Grafana HA
    └── Admin UI
```

## 4. Data Model Analysis

### 4.1 Event Schemas

#### **CDN Log Event**
```json
{
  "ts": "2025-01-20T12:00:00Z",
  "request_id": "req_abc123",
  "channel_id": "channel_xyz", 
  "session_id": "sess_456",
  "client_ip": "203.0.113.5",
  "user_agent": "Mozilla/5.0...",
  "referrer": "https://example.com",
  "host": "cdn.example.com",
  "path": "/stream/segment001.ts",
  "query": "CMCD=sid=\"sess_456\",br=1500",
  "status": 200,
  "ttfb_ms": 85,
  "resp_bytes": 1048576,
  "country": "US",
  "asn": 15169,
  "ja3": "abc123...",
  "ja4": "ja4h_xyz...",
  "cmcd": {
    "sid": "sess_456",
    "br": "1500",
    "bl": "8000",
    "mtp": "2000"
  }
}
```

#### **Player Event**
```json
{
  "ts": "2025-01-20T12:00:05Z",
  "session_id": "sess_456",
  "viewer_id": "viewer_789",
  "channel_id": "channel_xyz",
  "event_type": "level_switched",
  "bitrate": 1500000,
  "stall_ms": 0,
  "buffer_len_ms": 8000,
  "error_code": null
}
```

#### **Decision Event**
```json
{
  "ts": "2025-01-20T12:00:10Z",
  "session_id": "sess_456", 
  "channel_id": "channel_xyz",
  "score": 0.75,
  "reasons": ["lockstep_cadence", "datacenter_asn"],
  "action": "challenge",
  "model_version": "v1.2.3"
}
```

### 4.2 Feature Schema

#### **Session Features** (5-minute windows)
- `reqs_per_min`: Request frequency
- `unique_segments`: Distinct content fetched
- `avg_ttfb_ms`: Average Time to First Byte
- `cadence_std_ms`: Segment fetch timing variance
- `non200_rate`: Error rate percentage
- `cmcd_bl_avg`: Average buffer length (CMCD)
- `cmcd_br_changes`: Bitrate switch frequency
- `referrer_entropy`: Referrer diversity measure
- `ja4_rarity`: TLS fingerprint commonality
- `asn_type`: hosting/residential/mobile classification

#### **Channel Features** (aggregated)
- `ccv_raw`: Raw concurrent viewer count
- `ccv_adjusted`: Post-filtering viewer count
- `chat_msg_rate`: Messages per minute
- `unique_chatters`: Distinct chat participants
- `geo_entropy`: Geographic distribution measure
- `asn_concentration`: Top ASN percentage

## 5. Process Analysis

### 5.1 Core Business Processes

#### **Real-time Detection Process**
1. **Event Ingestion**: Receive CDN/player/chat events
2. **Session Correlation**: Link events to viewer sessions
3. **Feature Computation**: Calculate behavioral metrics
4. **Risk Assessment**: Apply ML models and rules
5. **Decision Making**: Determine enforcement action
6. **Action Execution**: Implement countermeasures
7. **Audit Logging**: Record decisions and outcomes

#### **Model Training Process**
1. **Data Collection**: Gather labeled examples
2. **Feature Engineering**: Transform raw events to ML features  
3. **Model Training**: Supervised learning with validation
4. **Performance Evaluation**: Precision/recall testing
5. **Model Deployment**: Update inference pipeline
6. **Monitoring**: Track model drift and performance

#### **Investigation Process**
1. **Alert Generation**: Anomaly detection triggers
2. **Data Aggregation**: Collect related evidence
3. **Pattern Analysis**: Identify bot farm characteristics
4. **Manual Review**: Human verification of findings
5. **Policy Update**: Refine detection rules
6. **Feedback Loop**: Update model training data

### 5.2 Exception Handling

#### **Data Quality Issues**
- **Missing Fields**: Default values, quality alerts
- **Schema Changes**: Backward compatibility, gradual migration
- **Duplicate Events**: Deduplication by event ID and timestamp
- **Late Arrivals**: Watermarking with acceptable lateness

#### **System Failures**
- **Kafka Downtime**: Local buffering, graceful degradation
- **ClickHouse Outage**: Read replica failover, cache serving
- **Flink Job Failure**: Checkpoint recovery, automatic restart
- **Model Serving Error**: Fallback to rule-based scoring

#### **Business Logic Edge Cases**
- **VPN Users**: ASN-based priors, not absolute blocks
- **Mobile Networks**: Carrier ASN allowlists, different thresholds
- **International Traffic**: Geo-specific baselines, local holidays
- **Live Events**: Spike detection tuning, manual overrides

## 6. Interface Analysis

### 6.1 External System Interfaces

#### **CDN Integration**
- **CloudFront**: Real-time logs via Kinesis Data Firehose
- **Fastly**: Log streaming API with custom VCL
- **Format**: JSON with configurable fields
- **Frequency**: Near real-time (1-5 second delay)
- **Volume**: 10K-100K events/second per CDN edge

#### **Player Integration** 
- **hls.js Events**: JavaScript callbacks for player state
- **CMCD Transport**: HTTP query parameters per CTA-5004
- **Custom Beacons**: XMLHttpRequest/Fetch API calls
- **Frequency**: Per segment (every 2-6 seconds)
- **Volume**: 1-10 events per viewer per minute

#### **Challenge Integration**
- **Cloudflare Turnstile**: Server-side token verification API
- **Request**: POST with token and secret key
- **Response**: JSON with success/failure and metadata
- **Latency**: <100ms for verification calls
- **Rate Limits**: 1000 verifications per minute

### 6.2 Internal APIs

#### **Risk Scoring API**
```
POST /api/v1/score
Content-Type: application/json

{
  "session_id": "sess_456",
  "features": { ... },
  "model_version": "v1.2.3"
}

Response:
{
  "score": 0.75,
  "reasons": ["lockstep_cadence", "datacenter_asn"],
  "action": "challenge",
  "timestamp": "2025-01-20T12:00:10Z"
}
```

#### **Enforcement API**
```
POST /api/v1/enforce
Content-Type: application/json

{
  "session_id": "sess_456",
  "action": "challenge",
  "ttl_seconds": 300
}

Response:
{
  "status": "accepted",
  "enforcement_id": "enf_789"
}
```

## 7. Performance Analysis

### 7.1 Throughput Requirements

| Component | Metric | Target | Current Capacity |
|-----------|--------|--------|------------------|
| **Kafka Ingestion** | Events/sec | 10,000 | 100,000+ |
| **Flink Processing** | Events/sec | 10,000 | 50,000+ |
| **ClickHouse Writes** | Rows/sec | 50,000 | 1,000,000+ |
| **API Responses** | Requests/sec | 1,000 | 10,000+ |

### 7.2 Latency Requirements

| Operation | Target | Typical |
|-----------|--------|---------|
| **End-to-end Detection** | <10s | 2-5s |
| **API Response** | <200ms | 50-100ms |
| **Dashboard Update** | <2s | 500ms-1s |
| **Challenge Verification** | <500ms | 100-200ms |

### 7.3 Storage Requirements

| Data Type | Daily Volume | Retention | Annual Total |
|-----------|--------------|-----------|--------------|
| **Raw Events** | 100GB | 90 days | 9TB |
| **Aggregated Metrics** | 10GB | 2 years | 7.3TB |
| **Decision Logs** | 1GB | 7 years | 2.6TB |
| **Total** | | | **~19TB** |

## 8. Security Analysis

### 8.1 Threat Model

#### **External Threats**
- **Bot Operators**: Sophisticated evasion techniques
- **DDoS Attacks**: Overwhelming system capacity
- **Data Exfiltration**: Unauthorized access to detection logic
- **Privacy Violations**: Exposure of user behavioral data

#### **Internal Threats**
- **Privilege Escalation**: Unauthorized system access
- **Data Manipulation**: Tampering with detection results
- **Configuration Errors**: Misconfiguration leading to vulnerabilities
- **Insider Threats**: Malicious employee actions

### 8.2 Security Controls

#### **Data Protection**
- **Encryption at Rest**: AES-256 for stored data
- **Encryption in Transit**: TLS 1.3 for all communications
- **Access Control**: RBAC with principle of least privilege
- **Data Masking**: PII scrubbing and anonymization

#### **System Security**
- **Container Scanning**: Vulnerability assessment of Docker images
- **Network Segmentation**: Isolated subnets for different tiers
- **API Security**: Rate limiting, authentication, input validation
- **Audit Logging**: Comprehensive activity tracking

#### **Operational Security**
- **Secrets Management**: HashiCorp Vault or similar
- **Configuration Management**: GitOps with encrypted configs
- **Incident Response**: Defined procedures and runbooks
- **Regular Updates**: Security patches and dependency updates

## 9. Scalability Analysis

### 9.1 Horizontal Scaling

#### **Stateless Components** (Easy scaling)
- **API Servers**: Load balancer + multiple instances
- **Flink TaskManagers**: Add nodes to increase parallelism
- **Grafana**: Multi-instance with shared storage

#### **Stateful Components** (Complex scaling)
- **Kafka/Redpanda**: Add brokers, rebalance partitions
- **ClickHouse**: Sharding and replication configuration
- **Redis**: Cluster mode with automatic failover

### 9.2 Vertical Scaling

| Component | CPU Scaling | Memory Scaling | Storage Scaling |
|-----------|-------------|----------------|-----------------|
| **Flink TaskManager** | Linear | Linear | Minimal |
| **ClickHouse** | Sublinear | Linear | Linear |
| **Kafka Broker** | Linear | Linear | Linear |
| **Redis** | Linear | Linear | N/A (in-memory) |

### 9.3 Geographic Distribution

#### **Multi-Region Deployment**
- **Data Locality**: Process events close to origin
- **Latency Optimization**: Regional Kafka clusters
- **Disaster Recovery**: Cross-region replication
- **Compliance**: Data residency requirements

## 10. Monitoring & Observability

### 10.1 System Metrics

#### **Business Metrics**
- Detection accuracy (precision/recall)
- Enforcement action distribution
- False positive/negative rates
- Bot farm discovery rate

#### **Technical Metrics**  
- Event processing latency
- System throughput (events/second)
- Error rates by component
- Resource utilization (CPU/memory/disk)

#### **Operational Metrics**
- System availability/uptime
- Alert response times
- Deployment frequency/success rate
- Mean time to recovery (MTTR)

### 10.2 Alerting Strategy

#### **Critical Alerts** (Immediate response)
- System outages or component failures
- Processing lag exceeding SLA thresholds
- High false positive rate spikes
- Security incidents or unauthorized access

#### **Warning Alerts** (Business hours response)
- Performance degradation trends
- Model drift indicators
- Data quality issues
- Capacity utilization thresholds

#### **Informational Alerts** (Periodic review)
- Daily/weekly summary reports
- Trend analysis notifications
- System optimization opportunities
- Usage pattern changes

## 11. Next Steps

This system analysis provides the foundation for detailed software design. Key outcomes:

1. ✅ **Architecture Validated**: Technology choices align with requirements
2. ✅ **Interfaces Defined**: Clear contracts between components  
3. ✅ **Data Model Established**: Schema design supports all use cases
4. ✅ **Performance Benchmarks**: Realistic targets with safety margins
5. ✅ **Security Framework**: Comprehensive threat mitigation strategy

**Ready to proceed to**: Detailed Software Design phase
```
graph TB
    subgraph "External Data Sources"
        CDN[CDN Logs<br/>CloudFront/Fastly]
        PLAYER[HLS Player<br/>hls.js + CMCD]
        CHAT[Chat Events<br/>IRC/EventSub]
        WAF[Edge Signals<br/>JA3/JA4/Challenges]
    end

    subgraph "Event Streaming Layer"
        KAFKA[Redpanda<br/>Kafka API]
    end

    subgraph "Real-time Processing"
        FLINK[Apache Flink<br/>Stream Processing]
        FEATURES[Feature Store<br/>Session State]
        SCORER[Risk Scorer<br/>ML + Rules]
    end

    subgraph "Storage Layer"
        CLICKHOUSE[ClickHouse<br/>Time-Series OLAP]
        REDIS[Redis<br/>Real-time Cache]
    end

    subgraph "Enforcement & Actions"
        ENFORCEMENT[Enforcement Engine]
        TURNSTILE[Cloudflare Turnstile<br/>Bot Challenges]
        ACTIONS[Actions<br/>Block/Challenge/Suppress]
    end

    subgraph "Monitoring & Dashboards"
        GRAFANA[Grafana Dashboards<br/>Real-time Metrics]
        ALERTS[Alert Manager<br/>Notifications]
        API[REST APIs<br/>External Integration]
    end

    CDN --> KAFKA
    PLAYER --> KAFKA
    CHAT --> KAFKA
    WAF --> KAFKA

    KAFKA --> FLINK
    FLINK --> FEATURES
    FEATURES --> SCORER
    SCORER --> CLICKHOUSE
    SCORER --> ENFORCEMENT

    ENFORCEMENT --> TURNSTILE
    ENFORCEMENT --> ACTIONS
    ENFORCEMENT --> REDIS

    CLICKHOUSE --> GRAFANA
    REDIS --> API
    GRAFANA --> ALERTS

    ACTIONS --> CDN
    TURNSTILE --> PLAYER

    classDef external fill:#e1f5fe
    classDef processing fill:#f3e5f5
    classDef storage fill:#e8f5e8
    classDef action fill:#fff3e0
    classDef monitoring fill:#fce4ec

    class CDN,PLAYER,CHAT,WAF external
    class KAFKA,FLINK,FEATURES,SCORER processing
    class CLICKHOUSE,REDIS storage
    class ENFORCEMENT,TURNSTILE,ACTIONS action
    class GRAFANA,ALERTS,API monitoring
```
