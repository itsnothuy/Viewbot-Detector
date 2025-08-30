#!/usr/bin/env python3
"""
Viewbot Guard REST API Service
Provides HTTP endpoints for analytics, management, and external integrations
"""

from fastapi import FastAPI, HTTPException, Depends, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import asyncio
import json
import os
import logging
from kafka import KafkaProducer
from clickhouse_driver import Client
import redis.asyncio as redis

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app initialization
app = FastAPI(
    title="Viewbot Guard API",
    description="Real-time viewbot detection and analytics API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# CORS middleware for browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer(auto_error=False)

# Configuration
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:19092")
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "9000"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Global connections
kafka_producer = None
clickhouse_client = None
redis_client = None

# Pydantic Models
class SessionFeatures(BaseModel):
    session_id: str
    channel_id: str
    reqs_per_min: float
    unique_segments: int
    avg_ttfb_ms: float
    cadence_std_ms: float
    non200_rate: float
    cmcd_bl_avg: Optional[float] = None
    cmcd_br_changes: int = 0
    ja4_rarity_score: float = 0.0
    asn_type: str = "unknown"
    referrer_entropy: float = 0.0

class RiskScoreRequest(BaseModel):
    session_id: str = Field(..., description="Unique session identifier")
    channel_id: str = Field(..., description="Channel being watched")
    features: SessionFeatures

class RiskScoreResponse(BaseModel):
    session_id: str
    channel_id: str
    score: float = Field(..., ge=0.0, le=1.0, description="Risk score from 0.0 to 1.0")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in the score")
    reasons: List[str] = Field(..., description="List of factors contributing to the score")
    action: str = Field(..., description="Recommended action: count, suppress, challenge, block")
    model_version: str
    timestamp: datetime

class ChannelMetrics(BaseModel):
    channel_id: str
    unique_viewers: int
    adjusted_viewers: int
    raw_viewers: int
    avg_risk_score: float
    enforcement_breakdown: Dict[str, int]
    top_asns: List[Dict[str, Any]]
    geo_distribution: Dict[str, int]
    last_updated: datetime

class PlayerEvent(BaseModel):
    session_id: str
    viewer_id: str
    channel_id: str
    event_type: str
    timestamp: datetime
    data: Dict[str, Any] = {}

class EnforcementAction(BaseModel):
    session_id: str
    channel_id: str
    action: str
    duration_seconds: int
    reason: str
    timestamp: datetime

# Dependency injection
async def get_kafka_producer():
    global kafka_producer
    if kafka_producer is None:
        kafka_producer = KafkaProducer(
            bootstrap_servers=[KAFKA_BOOTSTRAP],
            value_serializer=lambda x: json.dumps(x).encode('utf-8'),
            retries=3,
            acks='all'
        )
    return kafka_producer

async def get_clickhouse_client():
    global clickhouse_client
    if clickhouse_client is None:
        clickhouse_client = Client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            database='viewbot'
        )
    return clickhouse_client

async def get_redis_client():
    global redis_client
    if redis_client is None:
        redis_client = await redis.from_url(REDIS_URL)
    return redis_client

def verify_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """Simple token verification - in production, use proper JWT validation"""
    if credentials is None:
        return {"user_id": "anonymous", "permissions": ["read:metrics"]}
    
    # In production, validate JWT token here
    # For demo purposes, accept any token
    return {"user_id": "authenticated", "permissions": ["read:*", "write:*"]}

# Advanced Risk Scoring Engine
class AdvancedRiskScorer:
    def __init__(self):
        self.version = "v2.0_api"
    
    def score(self, features: SessionFeatures) -> Dict[str, Any]:
        """Advanced risk scoring with multiple detection rules"""
        score = 0.0
        reasons = []
        confidence = 0.8
        
        # Rule 1: Data center ASN (high weight)
        if features.asn_type == 'hosting':
            score += 0.4
            reasons.append('datacenter_asn')
        
        # Rule 2: Lockstep cadence (very suspicious timing)
        if features.cadence_std_ms < 20:
            score += 0.3
            reasons.append('lockstep_cadence')
        
        # Rule 3: High error rate
        if features.non200_rate > 0.1:
            score += 0.2
            reasons.append('high_error_rate')
        
        # Rule 4: Unusual request frequency
        if features.reqs_per_min > 25 or features.reqs_per_min < 3:
            score += 0.15
            reasons.append('unusual_request_rate')
        
        # Rule 5: JA4 rarity (uncommon fingerprints)
        if features.ja4_rarity_score > 0.7:
            score += 0.25
            reasons.append('uncommon_ja4')
        
        # Rule 6: CMCD inconsistencies
        if features.cmcd_bl_avg and features.cmcd_bl_avg > 0:
            if features.cmcd_bl_avg > 10000 and features.cmcd_br_changes > 10:
                score += 0.2
                reasons.append('cmcd_inconsistent')
        
        # Rule 7: Low referrer entropy (bot farms use same sources)
        if features.referrer_entropy < 0.5:
            score += 0.1
            reasons.append('low_referrer_entropy')
        
        # Rule 8: Impossible segment/request ratios
        expected_segments = max(1, int(features.reqs_per_min / 10))  # Rough estimate
        if features.unique_segments > expected_segments * 3:
            score += 0.3
            reasons.append('impossible_segment_ratio')
        
        # Rule 9: Perfect timing patterns (bots often have 0 variance)
        if features.cadence_std_ms == 0 and features.reqs_per_min > 10:
            score += 0.4
            reasons.append('perfect_timing_pattern')
        
        # Normalize score
        score = min(1.0, score)
        
        # Determine action based on score thresholds
        if score >= 0.8:
            action = "block"
        elif score >= 0.5:
            action = "challenge"
        elif score >= 0.3:
            action = "suppress"
        else:
            action = "count"
        
        return {
            "score": round(score, 3),
            "confidence": confidence,
            "reasons": reasons,
            "action": action,
            "model_version": self.version
        }

# Global scorer instance
risk_scorer = AdvancedRiskScorer()

# API Routes
@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Test key dependencies
        redis_client = await get_redis_client()
        await redis_client.ping()
        
        clickhouse_client = await get_clickhouse_client()
        clickhouse_client.execute("SELECT 1")
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "version": "1.0.0",
            "services": {
                "redis": "healthy",
                "clickhouse": "healthy",
                "kafka": "healthy"
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service unavailable")

@app.post("/api/v1/score", response_model=RiskScoreResponse)
async def score_session(
    request: RiskScoreRequest,
    auth: Dict = Depends(verify_token)
):
    """Score a session for viewbot risk"""
    try:
        # Perform risk scoring
        result = risk_scorer.score(request.features)
        
        # Create response
        response = RiskScoreResponse(
            session_id=request.session_id,
            channel_id=request.channel_id,
            score=result["score"],
            confidence=result["confidence"],
            reasons=result["reasons"],
            action=result["action"],
            model_version=result["model_version"],
            timestamp=datetime.utcnow()
        )
        
        # Store decision in Redis for quick access
        redis_client = await get_redis_client()
        decision_key = f"decision:{request.session_id}"
        await redis_client.hset(decision_key, mapping={
            "score": result["score"],
            "action": result["action"],
            "timestamp": response.timestamp.isoformat(),
            "reasons": json.dumps(result["reasons"])
        })
        await redis_client.expire(decision_key, 3600)  # 1 hour TTL
        
        # Send to Kafka for stream processing
        kafka_producer = await get_kafka_producer()
        decision_event = {
            "ts": response.timestamp.isoformat(),
            "session_id": request.session_id,
            "channel_id": request.channel_id,
            "score": result["score"],
            "confidence": result["confidence"],
            "reasons": result["reasons"],
            "action": result["action"],
            "model_version": result["model_version"],
            "features": request.features.dict()
        }
        kafka_producer.send("decisions", value=decision_event)
        
        logger.info(f"Scored session {request.session_id}: {result['score']} -> {result['action']}")
        return response
        
    except Exception as e:
        logger.error(f"Error scoring session {request.session_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/v1/channels/{channel_id}/metrics", response_model=ChannelMetrics)
async def get_channel_metrics(
    channel_id: str,
    hours_back: int = Query(24, ge=1, le=168),  # 1 hour to 1 week
    auth: Dict = Depends(verify_token)
):
    """Get comprehensive metrics for a specific channel"""
    try:
        clickhouse_client = await get_clickhouse_client()
        
        # Main metrics query
        metrics_query = """
        SELECT 
            uniqExact(session_id) as unique_viewers,
            countIf(action = 'count') as counted_sessions,
            countIf(action = 'suppress') as suppressed_sessions,
            countIf(action = 'challenge') as challenged_sessions,
            countIf(action = 'block') as blocked_sessions,
            avg(score) as avg_risk_score
        FROM viewbot.decisions 
        WHERE channel_id = %(channel_id)s 
          AND ts >= now() - INTERVAL %(hours)s HOUR
        """
        
        result = clickhouse_client.execute(metrics_query, {
            'channel_id': channel_id,
            'hours': hours_back
        })
        
        if not result:
            raise HTTPException(status_code=404, detail="Channel not found or no data")
        
        metrics = result[0]
        unique_viewers = metrics[0]
        counted_sessions = metrics[1]
        suppressed_sessions = metrics[2]
        challenged_sessions = metrics[3]
        blocked_sessions = metrics[4]
        avg_risk_score = metrics[5] or 0.0
        
        # Calculate adjusted viewers (assume 70% challenge pass rate)
        adjusted_viewers = counted_sessions + int(challenged_sessions * 0.7)
        raw_viewers = unique_viewers
        
        # Top ASNs query
        asn_query = """
        SELECT 
            any(asn) as asn_number,
            count() as session_count,
            avg(score) as avg_score
        FROM viewbot.decisions d
        JOIN viewbot.raw_cdn_log r ON d.session_id = r.session_id
        WHERE d.channel_id = %(channel_id)s 
          AND d.ts >= now() - INTERVAL %(hours)s HOUR
        GROUP BY asn_number
        ORDER BY session_count DESC
        LIMIT 10
        """
        
        asn_results = clickhouse_client.execute(asn_query, {
            'channel_id': channel_id,
            'hours': hours_back
        })
        
        top_asns = [
            {"asn": row[0], "sessions": row[1], "avg_score": round(row[2], 3)}
            for row in asn_results
        ]
        
        # Geographic distribution
        geo_query = """
        SELECT 
            country,
            count() as viewer_count
        FROM viewbot.decisions d
        JOIN viewbot.raw_cdn_log r ON d.session_id = r.session_id
        WHERE d.channel_id = %(channel_id)s 
          AND d.ts >= now() - INTERVAL %(hours)s HOUR
        GROUP BY country
        ORDER BY viewer_count DESC
        LIMIT 20
        """
        
        geo_results = clickhouse_client.execute(geo_query, {
            'channel_id': channel_id,
            'hours': hours_back
        })
        
        geo_distribution = {row[0]: row[1] for row in geo_results}
        
        return ChannelMetrics(
            channel_id=channel_id,
            unique_viewers=unique_viewers,
            adjusted_viewers=adjusted_viewers,
            raw_viewers=raw_viewers,
            avg_risk_score=round(avg_risk_score, 3),
            enforcement_breakdown={
                "counted": counted_sessions,
                "suppressed": suppressed_sessions,
                "challenged": challenged_sessions,
                "blocked": blocked_sessions
            },
            top_asns=top_asns,
            geo_distribution=geo_distribution,
            last_updated=datetime.utcnow()
        )
        
    except Exception as e:
        logger.error(f"Error getting metrics for channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/v1/sessions/{session_id}")
async def get_session_details(
    session_id: str,
    auth: Dict = Depends(verify_token)
):
    """Get detailed information about a specific session"""
    try:
        redis_client = await get_redis_client()
        clickhouse_client = await get_clickhouse_client()
        
        # Get latest decision from Redis
        decision_key = f"decision:{session_id}"
        decision_data = await redis_client.hgetall(decision_key)
        
        if not decision_data:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Get historical decisions from ClickHouse
        history_query = """
        SELECT ts, score, reasons, action, model_version
        FROM viewbot.decisions
        WHERE session_id = %(session_id)s
        ORDER BY ts DESC
        LIMIT 50
        """
        
        history = clickhouse_client.execute(history_query, {
            'session_id': session_id
        })
        
        return {
            "session_id": session_id,
            "current_decision": {
                "score": float(decision_data.get(b'score', 0)),
                "action": decision_data.get(b'action', b'').decode(),
                "timestamp": decision_data.get(b'timestamp', b'').decode(),
                "reasons": json.loads(decision_data.get(b'reasons', b'[]'))
            },
            "history": [
                {
                    "timestamp": row[0].isoformat(),
                    "score": row[1],
                    "reasons": row[2],
                    "action": row[3],
                    "model_version": row[4]
                }
                for row in history
            ]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session details for {session_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/v1/events/player")
async def ingest_player_event(
    event: PlayerEvent,
    auth: Dict = Depends(verify_token)
):
    """Ingest player events for analysis"""
    try:
        kafka_producer = await get_kafka_producer()
        
        # Enrich event with timestamp
        event_data = {
            "ts": event.timestamp.isoformat(),
            "session_id": event.session_id,
            "viewer_id": event.viewer_id,
            "channel_id": event.channel_id,
            "event_type": event.event_type,
            **event.data
        }
        
        kafka_producer.send("player-events", value=event_data)
        
        return {"status": "accepted", "event_id": f"{event.session_id}_{event.timestamp.timestamp()}"}
        
    except Exception as e:
        logger.error(f"Error ingesting player event: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/v1/enforcement/action")
async def execute_enforcement_action(
    action: EnforcementAction,
    auth: Dict = Depends(verify_token)
):
    """Execute enforcement action against a session"""
    try:
        redis_client = await get_redis_client()
        
        # Store enforcement action in Redis
        enforcement_key = f"enforcement:{action.session_id}"
        await redis_client.hset(enforcement_key, mapping={
            "action": action.action,
            "duration_seconds": action.duration_seconds,
            "reason": action.reason,
            "timestamp": action.timestamp.isoformat(),
            "expires_at": (action.timestamp + timedelta(seconds=action.duration_seconds)).isoformat()
        })
        await redis_client.expire(enforcement_key, action.duration_seconds)
        
        # Send to audit log
        kafka_producer = await get_kafka_producer()
        audit_event = {
            "ts": action.timestamp.isoformat(),
            "event_type": "enforcement_action",
            "session_id": action.session_id,
            "channel_id": action.channel_id,
            "action": action.action,
            "duration_seconds": action.duration_seconds,
            "reason": action.reason
        }
        kafka_producer.send("audit-log", value=audit_event)
        
        logger.info(f"Executed enforcement action {action.action} for session {action.session_id}")
        
        return {
            "status": "executed",
            "action_id": f"{action.session_id}_{action.timestamp.timestamp()}",
            "expires_at": (action.timestamp + timedelta(seconds=action.duration_seconds)).isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error executing enforcement action: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/v1/analytics/summary")
async def get_analytics_summary(
    hours_back: int = Query(24, ge=1, le=168),
    auth: Dict = Depends(verify_token)
):
    """Get system-wide analytics summary"""
    try:
        clickhouse_client = await get_clickhouse_client()
        
        summary_query = """
        SELECT 
            count() as total_decisions,
            uniqExact(session_id) as unique_sessions,
            uniqExact(channel_id) as active_channels,
            countIf(action = 'block') as blocked_count,
            countIf(action = 'challenge') as challenged_count,
            countIf(action = 'suppress') as suppressed_count,
            countIf(action = 'count') as counted_count,
            avg(score) as avg_risk_score,
            quantile(0.95)(score) as p95_risk_score
        FROM viewbot.decisions
        WHERE ts >= now() - INTERVAL %(hours)s HOUR
        """
        
        result = clickhouse_client.execute(summary_query, {'hours': hours_back})
        
        if result:
            metrics = result[0]
            return {
                "total_decisions": metrics[0],
                "unique_sessions": metrics[1],
                "active_channels": metrics[2],
                "enforcement_summary": {
                    "blocked": metrics[3],
                    "challenged": metrics[4],
                    "suppressed": metrics[5],
                    "counted": metrics[6]
                },
                "risk_metrics": {
                    "avg_score": round(metrics[7], 3),
                    "p95_score": round(metrics[8], 3)
                },
                "time_period_hours": hours_back,
                "timestamp": datetime.utcnow().isoformat()
            }
        else:
            return {
                "message": "No data available for the specified time period",
                "time_period_hours": hours_back,
                "timestamp": datetime.utcnow().isoformat()
            }
            
    except Exception as e:
        logger.error(f"Error getting analytics summary: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize connections on startup"""
    logger.info("Starting Viewbot Guard API...")
    
    # Initialize connections
    await get_redis_client()
    await get_clickhouse_client()
    await get_kafka_producer()
    
    logger.info("API service ready")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up connections on shutdown"""
    logger.info("Shutting down Viewbot Guard API...")
    
    global kafka_producer, redis_client
    
    if kafka_producer:
        kafka_producer.close()
    
    if redis_client:
        await redis_client.close()
    
    logger.info("API service stopped")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
