# Viewbot Guard - Software Design Document

## 1. Design Overview

### 1.1 Design Principles
- **Real-time Processing**: Sub-10 second detection latency requirement
- **Horizontal Scalability**: Handle 100K+ concurrent viewers
- **Fault Tolerance**: Graceful degradation under component failures  
- **Privacy by Design**: No PII storage, session-based analysis only
- **Explainable Decisions**: All detection outcomes must be auditable

### 1.2 Architecture Patterns
- **Event-Driven Architecture**: Asynchronous event processing via Kafka
- **CQRS**: Separate read/write models for analytics and operations
- **Circuit Breaker**: Prevent cascade failures between components
- **Bulkhead**: Isolate critical paths to maintain system stability
- **Saga Pattern**: Distributed transaction management for enforcement actions

### 1.3 Quality Attributes Priority
1. **Performance**: <10s detection latency, >10K events/sec throughput
2. **Reliability**: 99.9% availability, exactly-once processing guarantees  
3. **Scalability**: Linear scaling with hardware resources
4. **Security**: Defense in depth, encrypted data, access controls
5. **Maintainability**: Clean interfaces, comprehensive monitoring, documentation

## 2. System Architecture Design

### 2.1 Layered Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    PRESENTATION LAYER                       │
│  [Grafana Dashboards] [REST APIs] [Admin UI] [Webhooks]    │
└─────────────────────────────────────────────────────────────┘
                                │
┌─────────────────────────────────────────────────────────────┐
│                    APPLICATION LAYER                        │
│   [Risk Scorer] [Enforcement Engine] [Session Manager]     │
│   [Feature Computer] [Model Server] [Alert Manager]        │
└─────────────────────────────────────────────────────────────┘
                                │
┌─────────────────────────────────────────────────────────────┐
│                   DATA PROCESSING LAYER                     │
│     [Flink Jobs] [Stream Windows] [State Stores]          │
│     [Schema Registry] [Message Serialization]              │
└─────────────────────────────────────────────────────────────┘
                                │
┌─────────────────────────────────────────────────────────────┐
│                    DATA STORAGE LAYER                       │
│    [ClickHouse] [Redis] [Kafka] [Object Storage]          │
└─────────────────────────────────────────────────────────────┘
                                │
┌─────────────────────────────────────────────────────────────┐
│                   INFRASTRUCTURE LAYER                      │
│  [Docker/K8s] [Load Balancers] [Monitoring] [Networking]  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Microservices Architecture

#### **Core Services**

**1. Event Ingestion Service**
- **Responsibility**: Receive and validate external events
- **Technology**: Kafka Connect + Custom processors
- **Scaling**: Partition-based horizontal scaling
- **SLA**: 99.99% availability, <100ms processing latency

**2. Stream Processing Service** 
- **Responsibility**: Real-time feature computation and correlation
- **Technology**: Apache Flink cluster (JobManager + TaskManagers)
- **Scaling**: Dynamic TaskManager allocation based on throughput
- **SLA**: <5s end-to-end processing, exactly-once guarantees

**3. Risk Scoring Service**
- **Responsibility**: ML model inference and rule evaluation  
- **Technology**: Python/FastAPI with MLflow model serving
- **Scaling**: Containerized replicas behind load balancer
- **SLA**: <200ms inference latency, 99.9% availability

**4. Enforcement Service**
- **Responsibility**: Execute mitigation actions (block/challenge/suppress)
- **Technology**: Spring Boot with Redis state management
- **Scaling**: Stateless horizontal scaling
- **SLA**: <500ms action execution, 99.95% success rate

**5. Analytics Service**
- **Responsibility**: Historical analysis and dashboard data
- **Technology**: ClickHouse with materialized views
- **Scaling**: Sharded cluster with read replicas  
- **SLA**: <2s query response, 99.9% availability

#### **Supporting Services**

**6. Session Management Service**
- **Responsibility**: Viewer session lifecycle and state tracking
- **Technology**: Redis Cluster with TTL-based cleanup
- **Scaling**: Consistent hashing across cluster nodes
- **SLA**: <50ms read/write latency, 99.99% availability

**7. Challenge Service**
- **Responsibility**: Bot detection challenges and verification
- **Technology**: Node.js/Express with Cloudflare Turnstile integration
- **Scaling**: Stateless replicas with external API calls
- **SLA**: <300ms challenge verification, 99.9% availability

**8. Notification Service**
- **Responsibility**: Alerts, webhooks, and external notifications
- **Technology**: Go with message queue buffering
- **Scaling**: Worker pool pattern with job queues
- **SLA**: <1s notification delivery, 99.5% delivery success

### 2.3 Data Flow Architecture

#### **Event Processing Pipeline**
```
External Events → Kafka Topics → Schema Validation → Enrichment → 
Feature Extraction → Windowed Aggregation → Risk Scoring → 
Decision Engine → Action Execution → Audit Logging
```

#### **Stream Processing Topology**
```python
# Flink DataStream API pseudo-code
events = env.from_source(kafka_source, watermark_strategy, "events")

# Sessionization and correlation
keyed_events = events.key_by(lambda e: extract_session_key(e))

# Feature computation with sliding windows  
features = keyed_events \
    .window(SlidingEventTimeWindows.of(Time.minutes(5), Time.seconds(30))) \
    .aggregate(FeatureAggregator())

# Risk scoring
scores = features.map(RiskScoringFunction())

# Enforcement decisions
decisions = scores.process(DecisionProcessor())

# Output to multiple sinks
decisions.add_sink(kafka_sink("decisions"))
decisions.add_sink(clickhouse_sink("audit_log"))
```

## 3. Component Design

### 3.1 Risk Scoring Engine

#### **Interface Design**
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np

@dataclass
class SessionFeatures:
    session_id: str
    channel_id: str
    window_start: datetime
    
    # Transport features
    reqs_per_min: float
    unique_segments: int
    avg_ttfb_ms: float
    cadence_std_ms: float
    non200_rate: float
    
    # CMCD features  
    cmcd_bl_avg: Optional[float]
    cmcd_br_changes: int
    cmcd_mtp_consistency: float
    
    # Network features
    ja4_rarity_score: float
    asn_type: str  # 'hosting', 'residential', 'mobile'
    geo_consistency: float
    
    # Behavioral features
    referrer_entropy: float
    chat_engagement_ratio: float

@dataclass 
class RiskScore:
    session_id: str
    score: float  # 0.0 to 1.0
    confidence: float
    reasons: List[str]
    model_version: str
    timestamp: datetime

class RiskScorerInterface(ABC):
    @abstractmethod
    def score(self, features: SessionFeatures) -> RiskScore:
        pass
    
    @abstractmethod
    def get_model_info(self) -> Dict[str, Any]:
        pass

class EnsembleRiskScorer(RiskScorerInterface):
    def __init__(self, models: List[RiskScorerInterface], weights: List[float]):
        self.models = models
        self.weights = weights
        
    def score(self, features: SessionFeatures) -> RiskScore:
        scores = [model.score(features) for model in self.models]
        weighted_score = np.average([s.score for s in scores], weights=self.weights)
        
        combined_reasons = []
        for score in scores:
            combined_reasons.extend(score.reasons)
            
        return RiskScore(
            session_id=features.session_id,
            score=weighted_score,
            confidence=min(s.confidence for s in scores),
            reasons=list(set(combined_reasons)),
            model_version=f"ensemble_{len(self.models)}",
            timestamp=datetime.utcnow()
        )
```

#### **Model Implementations**

**Rule-Based Scorer**
```python
class RuleBasedScorer(RiskScorerInterface):
    def __init__(self, rules_config: Dict):
        self.rules = self._load_rules(rules_config)
    
    def score(self, features: SessionFeatures) -> RiskScore:
        score = 0.0
        reasons = []
        
        # Data center ASN rule
        if features.asn_type == 'hosting':
            score += 0.4
            reasons.append('datacenter_asn')
            
        # Lockstep cadence rule  
        if features.cadence_std_ms < 10:  # Too consistent
            score += 0.3
            reasons.append('lockstep_cadence')
            
        # High error rate rule
        if features.non200_rate > 0.1:
            score += 0.2  
            reasons.append('high_error_rate')
            
        # CMCD plausibility rule
        if features.cmcd_bl_avg and features.cmcd_bl_avg > 10000:
            if features.non200_rate > 0.05:  # Big buffer but many stalls
                score += 0.25
                reasons.append('cmcd_inconsistent')
        
        return RiskScore(
            session_id=features.session_id,
            score=min(1.0, score),
            confidence=0.9,
            reasons=reasons,
            model_version="rules_v1.0",
            timestamp=datetime.utcnow()
        )
```

**ML-Based Scorer**
```python
import joblib
from sklearn.ensemble import IsolationForest

class MLRiskScorer(RiskScorerInterface):
    def __init__(self, model_path: str):
        self.model = joblib.load(model_path)
        self.feature_names = [
            'reqs_per_min', 'cadence_std_ms', 'non200_rate',
            'ja4_rarity_score', 'cmcd_bl_avg', 'referrer_entropy'
        ]
        
    def score(self, features: SessionFeatures) -> RiskScore:
        # Convert features to numpy array
        feature_vector = np.array([
            getattr(features, fname, 0.0) for fname in self.feature_names
        ]).reshape(1, -1)
        
        # Get anomaly score (-1 to 1, where -1 is most anomalous)
        anomaly_score = self.model.decision_function(feature_vector)[0]
        
        # Convert to 0-1 risk score
        risk_score = max(0.0, (1.0 - anomaly_score) / 2.0)
        
        # Generate explanations based on feature importance
        reasons = self._explain_prediction(features, feature_vector)
        
        return RiskScore(
            session_id=features.session_id,
            score=risk_score,
            confidence=0.8,  # ML models have inherent uncertainty
            reasons=reasons,
            model_version="isolation_forest_v2.1", 
            timestamp=datetime.utcnow()
        )
        
    def _explain_prediction(self, features, feature_vector):
        # Simple rule-based explanation logic
        reasons = []
        
        if features.cadence_std_ms < 20:
            reasons.append('low_cadence_variance')
        if features.ja4_rarity_score > 0.8:
            reasons.append('uncommon_ja4')
        if features.non200_rate > 0.05:
            reasons.append('elevated_error_rate')
            
        return reasons
```

### 3.2 Enforcement Engine

#### **Interface Design**
```python
from enum import Enum
from dataclasses import dataclass

class ActionType(Enum):
    COUNT = "count"           # Normal counting
    SUPPRESS = "suppress"     # Don't count toward CCV  
    CHALLENGE = "challenge"   # Issue bot challenge
    BLOCK = "block"          # Block streaming access

@dataclass
class EnforcementAction:
    session_id: str
    channel_id: str
    action: ActionType
    duration_seconds: int
    reason: str
    timestamp: datetime
    
class EnforcementEngine:
    def __init__(self, redis_client, challenge_service):
        self.redis = redis_client
        self.challenge_service = challenge_service
        
    async def execute_action(self, decision: RiskScore) -> EnforcementAction:
        action_type = self._determine_action(decision.score)
        
        action = EnforcementAction(
            session_id=decision.session_id,
            channel_id=decision.channel_id, 
            action=action_type,
            duration_seconds=self._get_duration(action_type),
            reason=f"risk_score_{decision.score:.3f}",
            timestamp=datetime.utcnow()
        )
        
        await self._apply_action(action)
        await self._log_action(action)
        
        return action
        
    def _determine_action(self, score: float) -> ActionType:
        if score >= 0.8:
            return ActionType.BLOCK
        elif score >= 0.5:
            return ActionType.CHALLENGE  
        elif score >= 0.3:
            return ActionType.SUPPRESS
        else:
            return ActionType.COUNT
            
    async def _apply_action(self, action: EnforcementAction):
        key = f"enforcement:{action.session_id}"
        
        await self.redis.hset(key, {
            "action": action.action.value,
            "expires_at": action.timestamp + timedelta(seconds=action.duration_seconds),
            "reason": action.reason
        })
        
        await self.redis.expire(key, action.duration_seconds)
        
        # Special handling for challenges
        if action.action == ActionType.CHALLENGE:
            await self.challenge_service.issue_challenge(
                action.session_id, 
                action.duration_seconds
            )
```

### 3.3 Feature Computation Engine

#### **Flink DataStream Implementation**
```python
from pyflink.datastream import StreamExecutionEnvironment, WindowFunction
from pyflink.datastream.window import TumblingEventTimeWindows
import json

class FeatureAggregator(WindowFunction):
    def apply(self, window, inputs):
        events = list(inputs)
        
        if not events:
            return []
            
        # Extract session info from first event
        first_event = events[0]
        session_id = first_event.get('session_id')
        channel_id = first_event.get('channel_id')
        
        # Compute transport features
        request_times = [e.get('ts') for e in events if e.get('event_type') == 'segment_request']
        cadence_deltas = self._compute_cadence_deltas(request_times)
        
        features = {
            'session_id': session_id,
            'channel_id': channel_id, 
            'window_start': window.start,
            'window_end': window.end,
            
            # Request pattern features
            'reqs_per_min': len(events) / (window.end - window.start).seconds * 60,
            'unique_segments': len(set(e.get('path', '') for e in events)),
            'avg_ttfb_ms': np.mean([e.get('ttfb_ms', 0) for e in events]),
            'cadence_std_ms': np.std(cadence_deltas) if cadence_deltas else 0,
            'non200_rate': sum(1 for e in events if e.get('status') != 200) / len(events),
            
            # CMCD features
            'cmcd_bl_avg': self._compute_cmcd_avg(events, 'bl'),
            'cmcd_br_changes': self._count_bitrate_changes(events),
            'cmcd_mtp_consistency': self._compute_mtp_consistency(events),
            
            # Network features  
            'ja4_rarity_score': self._compute_ja4_rarity(first_event.get('ja4')),
            'asn_type': self._classify_asn(first_event.get('asn')),
            'geo_consistency': self._compute_geo_consistency(events),
            
            # Behavioral features
            'referrer_entropy': self._compute_referrer_entropy(events),
            'chat_engagement_ratio': 0.0  # Computed separately from chat events
        }
        
        return [SessionFeatures(**features)]
        
    def _compute_cadence_deltas(self, timestamps):
        if len(timestamps) < 2:
            return []
        
        sorted_times = sorted(timestamps)
        deltas = []
        
        for i in range(1, len(sorted_times)):
            delta_ms = (sorted_times[i] - sorted_times[i-1]).total_seconds() * 1000
            deltas.append(delta_ms)
            
        return deltas
        
    def _compute_cmcd_avg(self, events, key):
        values = []
        for event in events:
            cmcd = event.get('cmcd', {})
            if isinstance(cmcd, dict) and key in cmcd:
                try:
                    values.append(float(cmcd[key]))
                except (ValueError, TypeError):
                    continue
        return np.mean(values) if values else None
        
    def _count_bitrate_changes(self, events):
        bitrates = []
        for event in events:
            cmcd = event.get('cmcd', {})
            if isinstance(cmcd, dict) and 'br' in cmcd:
                try:
                    bitrates.append(int(cmcd['br']))
                except (ValueError, TypeError):
                    continue
        
        if len(bitrates) < 2:
            return 0
            
        changes = 0
        for i in range(1, len(bitrates)):
            if bitrates[i] != bitrates[i-1]:
                changes += 1
                
        return changes
```

### 3.4 Data Access Layer

#### **ClickHouse Repository Pattern**
```python
from clickhouse_driver import Client
from typing import List, Optional
import pandas as pd

class ClickHouseRepository:
    def __init__(self, host: str, port: int = 9000, database: str = 'viewbot'):
        self.client = Client(host=host, port=port, database=database)
        
    async def insert_decisions(self, decisions: List[RiskScore]):
        data = [
            (d.session_id, d.channel_id, d.score, d.reasons, 
             d.model_version, d.timestamp)
            for d in decisions
        ]
        
        query = '''
        INSERT INTO viewbot.decisions 
        (session_id, channel_id, score, reasons, model_version, timestamp)
        VALUES
        '''
        
        self.client.execute(query, data)
        
    async def get_channel_metrics(self, channel_id: str, 
                                hours_back: int = 24) -> Dict[str, Any]:
        query = '''
        SELECT 
            uniqExact(session_id) as unique_viewers,
            avg(score) as avg_risk_score,
            countIf(score >= 0.8) as blocked_sessions,
            countIf(score >= 0.5 AND score < 0.8) as challenged_sessions,
            countIf(score >= 0.3 AND score < 0.5) as suppressed_sessions,
            countIf(score < 0.3) as counted_sessions
        FROM viewbot.decisions 
        WHERE channel_id = %(channel_id)s 
          AND timestamp >= now() - INTERVAL %(hours)s HOUR
        '''
        
        result = self.client.execute(query, {
            'channel_id': channel_id,
            'hours': hours_back
        })
        
        if result:
            return {
                'unique_viewers': result[0][0],
                'avg_risk_score': result[0][1],
                'blocked_sessions': result[0][2],
                'challenged_sessions': result[0][3], 
                'suppressed_sessions': result[0][4],
                'counted_sessions': result[0][5],
                'adjusted_viewers': result[0][5] + result[0][3] * 0.7  # Assume 70% challenge pass rate
            }
        
        return {}
        
    async def get_top_risk_sessions(self, limit: int = 100) -> List[Dict]:
        query = '''
        SELECT session_id, channel_id, score, reasons, timestamp
        FROM viewbot.decisions 
        WHERE timestamp >= now() - INTERVAL 1 HOUR
        ORDER BY score DESC, timestamp DESC
        LIMIT %(limit)s
        '''
        
        results = self.client.execute(query, {'limit': limit})
        
        return [
            {
                'session_id': row[0],
                'channel_id': row[1],
                'score': row[2],
                'reasons': row[3],
                'timestamp': row[4]
            }
            for row in results
        ]
```

### 3.5 API Layer Design

#### **REST API Specification**
```python
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
import asyncio

app = FastAPI(title="Viewbot Guard API", version="1.0.0")

class SessionScoreRequest(BaseModel):
    session_id: str
    features: dict

class SessionScoreResponse(BaseModel):
    session_id: str
    score: float
    confidence: float
    reasons: List[str]
    action: str
    timestamp: str

class ChannelMetricsResponse(BaseModel):
    channel_id: str
    unique_viewers: int
    adjusted_viewers: int
    avg_risk_score: float
    enforcement_breakdown: dict
    last_updated: str

@app.post("/api/v1/score", response_model=SessionScoreResponse)
async def score_session(
    request: SessionScoreRequest,
    risk_scorer: RiskScorerInterface = Depends(get_risk_scorer)
):
    try:
        features = SessionFeatures(**request.features)
        risk_score = risk_scorer.score(features)
        
        # Determine action based on score
        if risk_score.score >= 0.8:
            action = "block"
        elif risk_score.score >= 0.5:
            action = "challenge"
        elif risk_score.score >= 0.3:
            action = "suppress"
        else:
            action = "count"
            
        return SessionScoreResponse(
            session_id=risk_score.session_id,
            score=risk_score.score,
            confidence=risk_score.confidence,
            reasons=risk_score.reasons,
            action=action,
            timestamp=risk_score.timestamp.isoformat()
        )
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/v1/channels/{channel_id}/metrics", response_model=ChannelMetricsResponse)
async def get_channel_metrics(
    channel_id: str,
    hours_back: int = 24,
    repository: ClickHouseRepository = Depends(get_repository)
):
    metrics = await repository.get_channel_metrics(channel_id, hours_back)
    
    if not metrics:
        raise HTTPException(status_code=404, detail="Channel not found")
        
    return ChannelMetricsResponse(
        channel_id=channel_id,
        unique_viewers=metrics['unique_viewers'],
        adjusted_viewers=int(metrics['adjusted_viewers']),
        avg_risk_score=metrics['avg_risk_score'],
        enforcement_breakdown={
            'blocked': metrics['blocked_sessions'],
            'challenged': metrics['challenged_sessions'],
            'suppressed': metrics['suppressed_sessions'],
            'counted': metrics['counted_sessions']
        },
        last_updated=datetime.utcnow().isoformat()
    )

@app.get("/api/v1/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}
```

## 4. Database Design

### 4.1 ClickHouse Schema Design

#### **Core Tables**
```sql
-- Raw CDN logs with all request details
CREATE TABLE viewbot.raw_cdn_log
(
    ts DateTime64(3),
    request_id String,
    channel_id String,
    session_id String,
    client_ip IPv4,
    user_agent String,
    referrer String,
    host String,
    path String,
    query String,
    status UInt16,
    ttfb_ms UInt32,
    resp_bytes UInt64,
    country FixedString(2),
    asn UInt32,
    ja3 String,
    ja4 String,
    cmcd Map(String, String)
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(ts)
ORDER BY (channel_id, session_id, ts)
TTL ts + INTERVAL 90 DAY;

-- Player events from hls.js
CREATE TABLE viewbot.player_events
(
    ts DateTime64(3),
    session_id String,
    viewer_id String,
    channel_id String,
    event_type String,
    bitrate UInt32,
    stall_ms UInt32,
    buffer_len_ms UInt32,
    error_code String
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(ts)
ORDER BY (channel_id, session_id, ts)
TTL ts + INTERVAL 90 DAY;

-- Risk scoring decisions with full audit trail
CREATE TABLE viewbot.decisions
(
    ts DateTime64(3) DEFAULT now(),
    session_id String,
    channel_id String,
    score Float32,
    confidence Float32,
    reasons Array(String),
    action Enum8('count' = 1, 'suppress' = 2, 'challenge' = 3, 'block' = 4),
    model_version String,
    features Map(String, String)
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(ts)
ORDER BY (channel_id, session_id, ts)
TTL ts + INTERVAL 7 YEAR;  -- Long retention for compliance

-- IP to ASN mapping for enrichment
CREATE TABLE viewbot.ip_asn
(
    ip_start IPv4,
    ip_end IPv4,
    asn UInt32,
    as_org String,
    country FixedString(2),
    is_hosting_provider UInt8
)
ENGINE = MergeTree
ORDER BY ip_start;
```

#### **Materialized Views for Real-time Analytics**
```sql
-- 5-minute viewer aggregations
CREATE MATERIALIZED VIEW viewbot.mv_viewer_5m
ENGINE = AggregatingMergeTree()
ORDER BY (channel_id, window_start, session_id)
AS
SELECT
    channel_id,
    session_id,
    toStartOfFiveMinute(ts) AS window_start,
    count() AS reqs,
    uniqExact(path) AS unique_segments,
    avg(ttfb_ms) AS avg_ttfb_ms,
    stddevSamp(ttfb_ms) AS ttfb_std_ms,
    sumIf(1, status != 200) AS non200s,
    avgState(toUInt32OrZero(cmcd['bl'])) AS avg_buffer_len,
    uniqExactState(referrer) AS uniq_referrers,
    anyLast(ja4) AS ja4_sample
FROM viewbot.raw_cdn_log
GROUP BY channel_id, session_id, window_start;

-- Channel-level hourly metrics
CREATE MATERIALIZED VIEW viewbot.mv_channel_1h  
ENGINE = SummingMergeTree()
ORDER BY (channel_id, window_start)
AS
SELECT
    channel_id,
    toStartOfHour(ts) AS window_start,
    uniqExact(session_id) AS unique_viewers,
    count() AS total_requests,
    avg(score) AS avg_risk_score,
    countIf(action = 'block') AS blocked_sessions,
    countIf(action = 'challenge') AS challenged_sessions,
    countIf(action = 'suppress') AS suppressed_sessions,
    countIf(action = 'count') AS counted_sessions
FROM viewbot.decisions
GROUP BY channel_id, window_start;
```

### 4.2 Redis Schema Design

#### **Session State Management**
```python
# Session tracking with TTL
session:sess_12345 = {
    "channel_id": "channel_abc",
    "first_seen": "2025-01-20T12:00:00Z",
    "last_activity": "2025-01-20T12:15:30Z",
    "request_count": 150,
    "current_action": "count",
    "risk_score": 0.25,
    "ja4": "ja4h_abc123..."
}
# TTL: 3600 seconds (1 hour of inactivity)

# Real-time viewer counts per channel
channel:viewers:channel_abc = {
    "raw_count": 12540,
    "adjusted_count": 11205,  # After bot filtering
    "last_updated": "2025-01-20T12:15:45Z"
}
# TTL: 300 seconds (5 minutes)

# Enforcement actions with expiration
enforcement:sess_12345 = {
    "action": "challenge",
    "reason": "risk_score_0.750",
    "expires_at": "2025-01-20T12:30:00Z",
    "challenge_issued": true
}
# TTL: Based on action duration

# JA4 fingerprint frequency tracking (for rarity scoring)
ja4:frequency:ja4h_abc123 = {
    "count": 1250,
    "first_seen": "2025-01-19T08:00:00Z", 
    "last_seen": "2025-01-20T12:15:45Z"
}
# TTL: 86400 seconds (24 hours)
```

## 5. Security Design

### 5.1 Authentication & Authorization

#### **API Security**
```python
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

security = HTTPBearer()

class SecurityService:
    def __init__(self, secret_key: str):
        self.secret_key = secret_key
        
    def verify_token(self, credentials: HTTPAuthorizationCredentials = Security(security)):
        try:
            payload = jwt.decode(
                credentials.credentials, 
                self.secret_key, 
                algorithms=["HS256"]
            )
            
            # Extract permissions
            permissions = payload.get("permissions", [])
            user_id = payload.get("sub")
            
            return {
                "user_id": user_id,
                "permissions": permissions
            }
            
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials"
            )

# Role-based permissions
PERMISSIONS = {
    "viewer": ["read:metrics"],
    "analyst": ["read:metrics", "read:decisions", "write:overrides"],
    "admin": ["read:*", "write:*", "delete:*"]
}
```

### 5.2 Data Encryption

#### **At Rest Encryption**
```yaml
# ClickHouse encryption configuration
clickhouse:
  encryption:
    key_management: "vault"
    algorithm: "AES-256-CTR" 
    key_rotation_days: 90

# Redis encryption
redis:
  tls:
    enabled: true
    cert_file: "/etc/ssl/redis.crt"
    key_file: "/etc/ssl/redis.key"
    ca_file: "/etc/ssl/ca.crt"
```

#### **In Transit Encryption**
```yaml
# TLS configuration for all services
tls:
  min_version: "1.3"
  cipher_suites:
    - "TLS_AES_256_GCM_SHA384"
    - "TLS_CHACHA20_POLY1305_SHA256"
    - "TLS_AES_128_GCM_SHA256"
  
# Kafka TLS/SASL configuration  
kafka:
  security_protocol: "SASL_SSL"
  sasl_mechanism: "PLAIN"
  ssl_ca_location: "/etc/ssl/ca.pem"
  ssl_certificate_location: "/etc/ssl/client.pem"
  ssl_key_location: "/etc/ssl/client-key.pem"
```

## 6. Monitoring & Observability Design

### 6.1 Metrics Collection

#### **Business Metrics**
```python
from prometheus_client import Counter, Histogram, Gauge

# Detection accuracy metrics
detection_precision = Gauge(
    'viewbot_detection_precision',
    'Precision rate of viewbot detection',
    ['model_version', 'time_window']
)

detection_recall = Gauge(
    'viewbot_detection_recall', 
    'Recall rate of viewbot detection',
    ['model_version', 'time_window']
)

# Enforcement action metrics
enforcement_actions_total = Counter(
    'viewbot_enforcement_actions_total',
    'Total number of enforcement actions taken',
    ['action_type', 'channel_id']
)

# Processing latency metrics  
processing_duration_seconds = Histogram(
    'viewbot_processing_duration_seconds',
    'Time spent processing events',
    ['component', 'operation']
)

# System health metrics
active_sessions = Gauge(
    'viewbot_active_sessions',
    'Number of active viewer sessions being tracked'
)

event_processing_rate = Gauge(
    'viewbot_events_per_second',
    'Rate of events being processed',
    ['event_type', 'source']
)
```

#### **Alerting Rules**
```yaml
groups:
  - name: viewbot_detection
    rules:
      - alert: HighFalsePositiveRate
        expr: viewbot_false_positive_rate > 0.02
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "False positive rate exceeded threshold"
          description: "False positive rate is {{ $value | humanizePercentage }}"
          
      - alert: DetectionLatencyHigh
        expr: histogram_quantile(0.95, viewbot_processing_duration_seconds) > 10
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Detection latency too high"
          description: "95th percentile latency is {{ $value }}s"
          
      - alert: SystemOverload
        expr: viewbot_events_per_second > 15000
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Event processing rate very high"
          description: "Processing {{ $value }} events/second"
```

### 6.2 Logging Strategy

#### **Structured Logging**
```python
import structlog
import json

logger = structlog.get_logger()

class DecisionLogger:
    def log_decision(self, decision: RiskScore, action: EnforcementAction):
        logger.info(
            "viewbot_decision_made",
            session_id=decision.session_id,
            channel_id=decision.channel_id,
            risk_score=decision.score,
            confidence=decision.confidence,
            reasons=decision.reasons,
            action=action.action.value,
            model_version=decision.model_version,
            timestamp=decision.timestamp.isoformat()
        )
        
    def log_enforcement_action(self, action: EnforcementAction, success: bool):
        logger.info(
            "viewbot_enforcement_executed",
            session_id=action.session_id,
            action_type=action.action.value,
            success=success,
            duration_seconds=action.duration_seconds,
            reason=action.reason,
            timestamp=action.timestamp.isoformat()
        )
```

## 7. Deployment Design

### 7.1 Container Architecture

#### **Docker Compose for Development**
```yaml
version: "3.8"
services:
  # Event streaming
  redpanda:
    image: redpanda/redpanda:v24.1.4
    container_name: viewbot-redpanda
    ports:
      - "9092:9092"
      - "19092:19092"
    volumes:
      - redpanda_data:/var/lib/redpanda/data
    
  # Analytics database
  clickhouse:
    image: clickhouse/clickhouse-server:23.8
    container_name: viewbot-clickhouse
    ports:
      - "8123:8123"
      - "9000:9000" 
    volumes:
      - ./storage/init:/docker-entrypoint-initdb.d
      - clickhouse_data:/var/lib/clickhouse
    environment:
      CLICKHOUSE_DB: viewbot
      CLICKHOUSE_USER: default
      
  # Stream processing
  flink-jobmanager:
    image: flink:1.18.1-scala_2.12-java11
    container_name: viewbot-jobmanager
    ports:
      - "8081:8081"
    command: jobmanager
    environment:
      FLINK_PROPERTIES: |
        jobmanager.rpc.address: flink-jobmanager
        
  flink-taskmanager:
    image: flink:1.18.1-scala_2.12-java11
    container_name: viewbot-taskmanager
    command: taskmanager
    environment:
      FLINK_PROPERTIES: |
        jobmanager.rpc.address: flink-jobmanager
        taskmanager.numberOfTaskSlots: 4
    depends_on:
      - flink-jobmanager
      
  # Session state storage
  redis:
    image: redis:7-alpine
    container_name: viewbot-redis  
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
      
  # Monitoring
  grafana:
    image: grafana/grafana:11.1.0
    container_name: viewbot-grafana
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./dashboards:/etc/grafana/provisioning/dashboards
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin

volumes:
  redpanda_data:
  clickhouse_data:
  redis_data:
  grafana_data:
```

### 7.2 Kubernetes Production Deployment

#### **Helm Chart Structure**
```yaml
# values.yaml
global:
  image:
    registry: "your-registry.com"
    tag: "1.0.0"
  
scaling:
  flink:
    taskmanagers:
      replicas: 4
      resources:
        requests:
          cpu: "1"
          memory: "2Gi"
        limits:
          cpu: "2" 
          memory: "4Gi"
          
  api:
    replicas: 3
    resources:
      requests:
        cpu: "500m"
        memory: "1Gi"
      limits:
        cpu: "1"
        memory: "2Gi"

storage:
  clickhouse:
    size: "1Ti"
    storageClass: "fast-ssd"
    
  redis:
    size: "100Gi"
    storageClass: "fast-ssd"

monitoring:
  prometheus:
    enabled: true
    retention: "30d"
    
  grafana:
    enabled: true
    persistence:
      enabled: true
      size: "10Gi"
```

## 8. Testing Strategy

### 8.1 Unit Testing

#### **Risk Scorer Tests**
```python
import pytest
import numpy as np
from datetime import datetime, timedelta

class TestRuleBasedScorer:
    def setup_method(self):
        self.scorer = RuleBasedScorer({})
        
    def test_datacenter_asn_detection(self):
        features = SessionFeatures(
            session_id="test_session",
            channel_id="test_channel", 
            window_start=datetime.utcnow(),
            reqs_per_min=60.0,
            unique_segments=30,
            avg_ttfb_ms=100.0,
            cadence_std_ms=50.0,
            non200_rate=0.01,
            cmcd_bl_avg=5000.0,
            cmcd_br_changes=2,
            cmcd_mtp_consistency=0.9,
            ja4_rarity_score=0.3,
            asn_type='hosting',  # This should trigger the rule
            geo_consistency=0.8,
            referrer_entropy=2.5,
            chat_engagement_ratio=0.1
        )
        
        result = self.scorer.score(features)
        
        assert result.score >= 0.4
        assert 'datacenter_asn' in result.reasons
        assert result.session_id == "test_session"
        
    def test_lockstep_cadence_detection(self):
        features = SessionFeatures(
            session_id="test_session",
            channel_id="test_channel",
            window_start=datetime.utcnow(),
            reqs_per_min=60.0,
            unique_segments=30,
            avg_ttfb_ms=100.0,
            cadence_std_ms=5.0,  # Very low variance - suspicious
            non200_rate=0.01,
            cmcd_bl_avg=5000.0,
            cmcd_br_changes=2,
            cmcd_mtp_consistency=0.9,
            ja4_rarity_score=0.3,
            asn_type='residential',
            geo_consistency=0.8,
            referrer_entropy=2.5,
            chat_engagement_ratio=0.1
        )
        
        result = self.scorer.score(features)
        
        assert result.score >= 0.3
        assert 'lockstep_cadence' in result.reasons
```

### 8.2 Integration Testing

#### **End-to-End Pipeline Test**
```python
import asyncio
import json
from kafka import KafkaProducer
from testcontainers import DockerCompose

class TestViewbotPipeline:
    @pytest.fixture(scope="class")
    def docker_services(self):
        compose = DockerCompose(".", compose_file_name="docker-compose.test.yml")
        compose.start()
        yield compose
        compose.stop()
        
    @pytest.mark.asyncio
    async def test_end_to_end_detection(self, docker_services):
        # Send synthetic bot traffic
        producer = KafkaProducer(
            bootstrap_servers=['localhost:19092'],
            value_serializer=lambda x: json.dumps(x).encode('utf-8')
        )
        
        # Create bot-like events (perfect cadence, datacenter ASN)
        bot_events = []
        base_time = datetime.utcnow()
        
        for i in range(60):  # 60 perfect requests
            event = {
                "ts": (base_time + timedelta(seconds=i*6)).isoformat(),  # Perfect 6s cadence
                "session_id": "bot_session_123",
                "channel_id": "test_channel",
                "client_ip": "1.2.3.4",
                "asn": 16509,  # Amazon ASN (hosting provider)
                "ja4": "ja4h_bot_fingerprint",
                "path": f"/stream/segment{i:03d}.ts",
                "status": 200,
                "ttfb_ms": 85,  # Consistent TTFB
                "cmcd": {"sid": "bot_session_123", "br": "1500"}
            }
            bot_events.append(event)
            
        # Send events to Kafka
        for event in bot_events:
            producer.send('cdn-logs', value=event)
            
        producer.flush()
        
        # Wait for processing
        await asyncio.sleep(30)
        
        # Check detection results
        from clickhouse_driver import Client
        client = Client('localhost', port=9000, database='viewbot')
        
        results = client.execute('''
            SELECT session_id, score, reasons, action
            FROM viewbot.decisions 
            WHERE session_id = 'bot_session_123'
            ORDER BY ts DESC
            LIMIT 1
        ''')
        
        assert len(results) > 0
        session_id, score, reasons, action = results[0]
        
        assert session_id == "bot_session_123"
        assert score >= 0.5  # Should be flagged as suspicious
        assert 'datacenter_asn' in reasons
        assert 'lockstep_cadence' in reasons
        assert action in ['challenge', 'block']
```

### 8.3 Load Testing

#### **Performance Test Scenarios**
```python
import asyncio
import aiohttp
import time
from concurrent.futures import ThreadPoolExecutor

class LoadTestSuite:
    async def test_api_throughput(self):
        """Test API can handle target RPS"""
        async with aiohttp.ClientSession() as session:
            tasks = []
            start_time = time.time()
            
            # Simulate 1000 requests/second for 60 seconds
            for _ in range(60000):
                task = self.make_api_request(session)
                tasks.append(task)
                
                # Rate limiting - 1000 RPS
                if len(tasks) % 1000 == 0:
                    await asyncio.sleep(1)
                    
            results = await asyncio.gather(*tasks, return_exceptions=True)
            end_time = time.time()
            
            # Analyze results
            successful = sum(1 for r in results if not isinstance(r, Exception))
            failed = len(results) - successful
            duration = end_time - start_time
            actual_rps = len(results) / duration
            
            assert actual_rps >= 900  # Allow 10% degradation
            assert failed / len(results) < 0.01  # <1% error rate
            
    async def make_api_request(self, session):
        """Make a single API request"""
        payload = {
            "session_id": f"load_test_{time.time()}",
            "features": {
                "reqs_per_min": 60.0,
                "cadence_std_ms": 50.0,
                "ja4_rarity_score": 0.3,
                # ... other features
            }
        }
        
        async with session.post(
            'http://localhost:8000/api/v1/score',
            json=payload,
            timeout=aiohttp.ClientTimeout(total=5)
        ) as response:
            return await response.json()
```

## 9. Next Steps

This comprehensive software design document provides:

1. ✅ **Detailed Component Specifications**: All major components designed with interfaces
2. ✅ **Database Schemas**: Complete ClickHouse and Redis schema definitions  
3. ✅ **API Contracts**: REST API specifications with request/response models
4. ✅ **Security Design**: Authentication, authorization, and encryption strategies
5. ✅ **Deployment Architecture**: Container and Kubernetes deployment specifications
6. ✅ **Testing Strategy**: Unit, integration, and load testing approaches

**Ready for**: Implementation phase with concrete code development

The design prioritizes:
- **Scalability**: Linear scaling with clear bottleneck identification
- **Reliability**: Fault tolerance and graceful degradation
- **Maintainability**: Clean interfaces and comprehensive monitoring
- **Security**: Defense in depth with encryption and access controls  
- **Performance**: Sub-10s detection with high throughput capabilities
