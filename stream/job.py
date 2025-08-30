from pyflink.datastream import StreamExecutionEnvironment, Time, CheckpointingMode
from pyflink.datastream.connectors.kafka import KafkaSource, KafkaSink, KafkaRecordSerializationSchema, KafkaRecordDeserializationSchema
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream import WatermarkStrategy
from pyflink.common.typeinfo import Types
from pyflink.datastream.functions import WindowFunction, ProcessFunction
from pyflink.datastream.window import TumblingEventTimeWindows
import json, time, statistics, math
from typing import Dict, Any, List
from datetime import datetime, timedelta

BOOTSTRAP = "redpanda:9092"

class SessionFeatures:
    def __init__(self, session_id: str, channel_id: str, window_start: int, window_end: int):
        self.session_id = session_id
        self.channel_id = channel_id
        self.window_start = window_start
        self.window_end = window_end
        
        # Transport features
        self.reqs_per_min = 0.0
        self.unique_segments = 0
        self.avg_ttfb_ms = 0.0
        self.cadence_std_ms = 0.0
        self.non200_rate = 0.0
        
        # CMCD features
        self.cmcd_bl_avg = 0.0
        self.cmcd_br_changes = 0
        self.cmcd_mtp_consistency = 0.0
        
        # Network features
        self.ja4_rarity_score = 0.0
        self.asn_type = 'unknown'
        self.geo_consistency = 1.0
        
        # Behavioral features
        self.referrer_entropy = 0.0

class AdvancedFeatureAggregator(WindowFunction):
    def apply(self, window, inputs):
        events = list(inputs)
        if not events:
            return []
            
        first_event = events[0]
        session_id = first_event.get('session_id', 'unknown')
        channel_id = first_event.get('channel_id', 'unknown')
        
        features = SessionFeatures(
            session_id=session_id,
            channel_id=channel_id,
            window_start=window.start,
            window_end=window.end
        )
        
        # Calculate transport features
        request_count = len(events)
        window_duration_min = (window.end - window.start) / 60000.0  # Convert ms to minutes
        features.reqs_per_min = request_count / window_duration_min if window_duration_min > 0 else 0
        
        # Unique segments
        segment_paths = set()
        for event in events:
            path = event.get('path', '')
            if path.endswith('.ts') or path.endswith('.m4s'):
                segment_paths.add(path)
        features.unique_segments = len(segment_paths)
        
        # TTFB statistics
        ttfbs = [float(e.get('ttfb_ms', 0)) for e in events if e.get('ttfb_ms')]
        if ttfbs:
            features.avg_ttfb_ms = statistics.mean(ttfbs)
        
        # Cadence analysis (timing between requests)
        timestamps = []
        for event in events:
            ts_str = event.get('ts', '')
            if ts_str:
                try:
                    timestamp = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    timestamps.append(timestamp.timestamp() * 1000)  # Convert to milliseconds
                except:
                    pass
        
        if len(timestamps) >= 2:
            timestamps.sort()
            deltas = []
            for i in range(1, len(timestamps)):
                delta = timestamps[i] - timestamps[i-1]
                deltas.append(delta)
            
            if deltas:
                features.cadence_std_ms = statistics.stdev(deltas) if len(deltas) > 1 else 0
        
        # Error rate
        non200_count = sum(1 for e in events if e.get('status', 200) != 200)
        features.non200_rate = non200_count / request_count if request_count > 0 else 0
        
        # CMCD analysis
        cmcd_bl_values = []
        bitrates = []
        
        for event in events:
            cmcd = event.get('cmcd', {})
            if isinstance(cmcd, dict):
                # Buffer length
                if 'bl' in cmcd:
                    try:
                        bl_value = float(cmcd['bl'])
                        cmcd_bl_values.append(bl_value)
                    except (ValueError, TypeError):
                        pass
                
                # Bitrate tracking for changes
                if 'br' in cmcd:
                    try:
                        br_value = int(cmcd['br'])
                        bitrates.append(br_value)
                    except (ValueError, TypeError):
                        pass
        
        if cmcd_bl_values:
            features.cmcd_bl_avg = statistics.mean(cmcd_bl_values)
        
        # Count bitrate changes
        if len(bitrates) > 1:
            changes = 0
            for i in range(1, len(bitrates)):
                if bitrates[i] != bitrates[i-1]:
                    changes += 1
            features.cmcd_br_changes = changes
        
        # Network fingerprinting
        ja4 = first_event.get('ja4', '')
        if ja4:
            # Simple rarity score based on JA4 characteristics
            # In production, this would lookup against a frequency table
            features.ja4_rarity_score = self._calculate_ja4_rarity(ja4)
        
        # ASN classification
        asn = first_event.get('asn', 0)
        features.asn_type = self._classify_asn(asn)
        
        # Referrer entropy
        referrers = [e.get('referrer', '') for e in events if e.get('referrer')]
        if referrers:
            features.referrer_entropy = self._calculate_entropy(referrers)
        
        return [features]
    
    def _calculate_ja4_rarity(self, ja4: str) -> float:
        """Calculate JA4 rarity score (0=common, 1=rare)"""
        # Simplified scoring based on JA4 characteristics
        # Real implementation would use historical frequency data
        if not ja4:
            return 0.5
        
        # Very basic heuristics for demonstration
        if 'bot' in ja4.lower() or len(ja4) < 20:
            return 0.9  # Likely bot fingerprint
        elif ja4.startswith('ja4h_'):
            return 0.3  # Common browser pattern
        else:
            return 0.6  # Unknown pattern, moderately suspicious
    
    def _classify_asn(self, asn: int) -> str:
        """Classify ASN as hosting/residential/mobile"""
        # Simplified classification - in production use MaxMind or similar
        hosting_asns = {
            16509,  # Amazon AWS
            13335,  # Cloudflare
            15169,  # Google Cloud
            8075,   # Microsoft Azure
            14061,  # DigitalOcean
        }
        
        if asn in hosting_asns:
            return 'hosting'
        elif asn == 0:
            return 'unknown'
        else:
            return 'residential'  # Assume residential for unknown ASNs
    
    def _calculate_entropy(self, values: List[str]) -> float:
        """Calculate Shannon entropy of string list"""
        if not values:
            return 0.0
        
        # Count occurrences
        counts = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        
        # Calculate entropy
        total = len(values)
        entropy = 0.0
        for count in counts.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
        
        return entropy

class AdvancedRiskScorer:
    def __init__(self):
        self.version = "v2.0_advanced"
    
    def score(self, features: SessionFeatures) -> Dict[str, Any]:
        score = 0.0
        reasons = []
        confidence = 0.8
        
        # Rule 1: Data center ASN (high weight)
        if features.asn_type == 'hosting':
            score += 0.4
            reasons.append('datacenter_asn')
        
        # Rule 2: Lockstep cadence (very suspicious timing)
        if features.cadence_std_ms < 20:  # Very consistent timing
            score += 0.3
            reasons.append('lockstep_cadence')
        
        # Rule 3: High error rate
        if features.non200_rate > 0.1:
            score += 0.2
            reasons.append('high_error_rate')
        
        # Rule 4: Unusual request frequency
        if features.reqs_per_min > 20 or features.reqs_per_min < 5:
            score += 0.15
            reasons.append('unusual_request_rate')
        
        # Rule 5: JA4 rarity
        if features.ja4_rarity_score > 0.7:
            score += 0.25
            reasons.append('uncommon_ja4')
        
        # Rule 6: CMCD inconsistencies
        if features.cmcd_bl_avg > 0:
            # Large buffer but many bitrate changes might indicate bot
            if features.cmcd_bl_avg > 10000 and features.cmcd_br_changes > 10:
                score += 0.2
                reasons.append('cmcd_inconsistent')
        
        # Rule 7: Low referrer entropy (all from same source)
        if features.referrer_entropy < 0.5:
            score += 0.1
            reasons.append('low_referrer_entropy')
        
        # Rule 8: Impossible segment diversity
        if features.unique_segments > features.reqs_per_min * 2:  # More unique segments than possible
            score += 0.3
            reasons.append('impossible_segment_ratio')
        
        # Normalize score
        score = min(1.0, score)
        
        # Determine action
        if score >= 0.8:
            action = "block"
        elif score >= 0.5:
            action = "challenge"
        elif score >= 0.3:
            action = "suppress"
        else:
            action = "count"
        
        return {
            "ts": int(time.time()),
            "session_id": features.session_id,
            "channel_id": features.channel_id,
            "score": round(score, 3),
            "confidence": confidence,
            "reasons": reasons,
            "action": action,
            "model_version": self.version,
            "features": {
                "reqs_per_min": features.reqs_per_min,
                "cadence_std_ms": features.cadence_std_ms,
                "non200_rate": features.non200_rate,
                "ja4_rarity_score": features.ja4_rarity_score,
                "asn_type": features.asn_type,
                "cmcd_bl_avg": features.cmcd_bl_avg
            }
        }

def main():
    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(2)  # Increased parallelism for better performance
    env.enable_checkpointing(30000, CheckpointingMode.EXACTLY_ONCE)  # 30s checkpoints
    
    def make_source(topic):
        return KafkaSource.builder() \
            .set_bootstrap_servers(BOOTSTRAP) \
            .set_topics(topic) \
            .set_group_id("viewbot-guard") \
            .set_value_only_deserializer(KafkaRecordDeserializationSchema.value_only(SimpleStringSchema())) \
            .build()

    # Create multiple streams for different event types
    cdn_stream = env.from_source(make_source("cdn-logs"), WatermarkStrategy.no_watermarks(), "cdn-logs") \
        .map(lambda s: json.loads(s), Types.MAP(Types.STRING(), Types.STRING())) \
        .filter(lambda e: e.get("session_id") and e.get("channel_id"))  # Filter valid events

    # Key by session for proper sessionization
    keyed_stream = cdn_stream.key_by(lambda e: f"{e.get('channel_id')}:{e.get('session_id')}")

    # Apply windowed feature aggregation (5-minute tumbling windows)
    features_stream = keyed_stream \
        .time_window(Time.minutes(5)) \
        .apply(AdvancedFeatureAggregator())

    # Apply risk scoring
    scorer = AdvancedRiskScorer()
    decisions_stream = features_stream.map(
        lambda f: json.dumps(scorer.score(f)), 
        Types.STRING()
    )

    # Sink to multiple destinations
    decisions_sink = KafkaSink.builder() \
        .set_bootstrap_servers(BOOTSTRAP) \
        .set_record_serializer(KafkaRecordSerializationSchema.builder() \
            .set_topic("decisions") \
            .set_value_serialization_schema(SimpleStringSchema()) \
            .build()) \
        .build()

    decisions_stream.sink_to(decisions_sink)
    
    # Also sink to audit topic for compliance
    audit_sink = KafkaSink.builder() \
        .set_bootstrap_servers(BOOTSTRAP) \
        .set_record_serializer(KafkaRecordSerializationSchema.builder() \
            .set_topic("audit-log") \
            .set_value_serialization_schema(SimpleStringSchema()) \
            .build()) \
        .build()

    decisions_stream.sink_to(audit_sink)
    
    env.execute("viewbot-guard-advanced")

if __name__ == "__main__":
    main()
