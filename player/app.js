// Enhanced player with comprehensive analytics and CMCD telemetry
class ViewbotGuardPlayer {
  constructor() {
    this.sessionId = this.generateSessionId();
    this.channelId = 'demo-channel'; // In production, extract from URL
    this.startTime = Date.now();
    this.segmentRequests = [];
    this.playerEvents = [];
    this.lastBufferCheck = 0;
    this.bitrate = 1500;
    this.measuredThroughput = 0;
  }

  generateSessionId() {
    return 'sess_' + crypto.getRandomValues(new Uint32Array(4))
      .reduce((acc, val) => acc + val.toString(16), '');
  }

  log(message) {
    const timestamp = new Date().toISOString();
    document.getElementById('log').textContent += `[${timestamp}] ${message}\n`;
    document.getElementById('log').scrollTop = document.getElementById('log').scrollHeight;
  }

  // Enhanced CMCD parameter generation
  generateCMCD(context, video) {
    const now = Date.now();
    let bufferLength = 0;
    
    try {
      if (video.buffered.length > 0) {
        bufferLength = Math.round((video.buffered.end(video.buffered.length - 1) - video.currentTime) * 1000);
      }
    } catch (e) {
      bufferLength = 0;
    }

    const cmcdParams = {
      // Session identification
      sid: this.sessionId,
      
      // Object type and format
      ot: context.url.includes('.m3u8') ? 'm' : 'v', // manifest or video
      sf: context.url.includes('.m3u8') ? 'l' : 'h', // live or HLS
      
      // Bitrate and throughput
      br: this.bitrate,
      bl: Math.max(0, bufferLength),
      
      // Measured throughput (updated from previous requests)
      ...(this.measuredThroughput > 0 && { mtp: this.measuredThroughput }),
      
      // Request timing
      rtp: Math.max(0, now - this.lastBufferCheck)
    };

    this.lastBufferCheck = now;
    return this.cmcdQuery(cmcdParams);
  }

  cmcdQuery(params) {
    const parts = [];
    for (const [k, v] of Object.entries(params)) {
      if (v !== null && v !== undefined) {
        const needsQuotes = typeof v === 'string' && !/^\d+$/.test(v);
        parts.push(`${k}=${needsQuotes ? `"${v}"` : v}`);
      }
    }
    return 'CMCD=' + encodeURIComponent(parts.join(','));
  }

  // Send player events to analytics endpoint
  async sendPlayerEvent(eventType, data = {}) {
    const event = {
      ts: new Date().toISOString(),
      session_id: this.sessionId,
      viewer_id: `viewer_${this.sessionId}`, // In production, use actual viewer ID
      channel_id: this.channelId,
      event_type: eventType,
      ...data
    };

    this.playerEvents.push(event);

    // In production, send to Kafka via REST API
    try {
      // For now, just log locally - in production this would POST to /api/v1/events
      console.log('Player event:', event);
    } catch (e) {
      console.warn('Failed to send player event:', e);
    }
  }

  // Calculate network throughput from segment download timing
  updateThroughput(startTime, endTime, bytes) {
    const duration = (endTime - startTime) / 1000; // Convert to seconds
    if (duration > 0) {
      const bitsPerSecond = (bytes * 8) / duration;
      const kbps = Math.round(bitsPerSecond / 1000);
      
      // Exponential moving average for smoother throughput measurement
      this.measuredThroughput = this.measuredThroughput === 0 
        ? kbps 
        : Math.round(0.7 * this.measuredThroughput + 0.3 * kbps);
      
      this.log(`Throughput updated: ${kbps} kbps (avg: ${this.measuredThroughput} kbps)`);
    }
  }

  // Enhanced fetch setup with detailed timing and analytics
  createFetchSetup(video) {
    return (context, initParams) => {
      const requestStart = performance.now();
      
      try {
        const url = new URL(context.url, window.location.href);
        const cmcd = this.generateCMCD(context, video);
        
        // Add CMCD as query parameter (browser-friendly approach)
        url.search += (url.search ? '&' : '?') + cmcd;
        
        const request = new Request(url.toString(), {
          ...initParams,
          headers: {
            ...initParams.headers,
            'User-Agent': navigator.userAgent,
            'X-Session-ID': this.sessionId
          }
        });

        // Track segment request timing
        this.segmentRequests.push({
          url: context.url,
          startTime: requestStart,
          timestamp: Date.now()
        });

        return request;
        
      } catch (e) {
        console.warn('CMCD injection error:', e);
        this.log(`CMCD Error: ${e.message}`);
        return new Request(context.url, initParams);
      }
    };
  }

  async start() {
    const video = document.getElementById('video');
    const url = document.getElementById('src').value;

    this.log(`Starting playback for session ${this.sessionId}`);
    this.log(`Channel: ${this.channelId}, URL: ${url}`);

    if (Hls.isSupported()) {
      const hls = new Hls({
        lowLatencyMode: true,
        progressive: false,
        enableWorker: true,
        lowLatencyMode: true,
        backBufferLength: 90,
        fetchSetup: this.createFetchSetup(video)
      });

      // Comprehensive event handling
      hls.on(Hls.Events.MANIFEST_LOADED, (evt, data) => {
        this.log(`Manifest loaded: ${data.levels.length} quality levels`);
        this.sendPlayerEvent('manifest_loaded', { 
          levels: data.levels.length,
          duration: data.totalduration 
        });
      });

      hls.on(Hls.Events.LEVEL_SWITCHED, (evt, data) => {
        this.bitrate = data.level !== -1 ? hls.levels[data.level].bitrate : 1500;
        this.log(`Quality switched to level ${data.level} (${this.bitrate} bps)`);
        this.sendPlayerEvent('level_switched', { 
          level: data.level,
          bitrate: this.bitrate
        });
      });

      hls.on(Hls.Events.FRAG_LOADED, (evt, data) => {
        const loadTime = data.stats.loading.end - data.stats.loading.start;
        const bytes = data.stats.loaded;
        
        this.updateThroughput(data.stats.loading.start, data.stats.loading.end, bytes);
        
        this.log(`Segment loaded: ${data.frag.sn}, ${bytes} bytes, ${loadTime}ms`);
        this.sendPlayerEvent('segment_loaded', {
          segment_sn: data.frag.sn,
          bytes: bytes,
          load_time_ms: loadTime,
          ttfb_ms: data.stats.loading.first - data.stats.loading.start
        });
      });

      hls.on(Hls.Events.ERROR, (evt, data) => {
        const errorMsg = `${data.type}/${data.details}`;
        this.log(`HLS Error: ${errorMsg}`);
        this.sendPlayerEvent('error', {
          error_type: data.type,
          error_code: data.details,
          fatal: data.fatal
        });

        if (data.fatal) {
          this.log('Fatal error - attempting recovery');
          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              hls.startLoad();
              break;
            case Hls.ErrorTypes.MEDIA_ERROR:
              hls.recoverMediaError();
              break;
            default:
              this.log('Unrecoverable error');
              break;
          }
        }
      });

      hls.on(Hls.Events.BUFFER_EMPTY, () => {
        this.log('Buffer empty - potential stall');
        this.sendPlayerEvent('buffer_empty', {
          current_time: video.currentTime,
          buffer_length: this.getBufferLength(video)
        });
      });

      hls.on(Hls.Events.BUFFER_EOS, () => {
        this.log('End of stream reached');
        this.sendPlayerEvent('buffer_eos');
      });

      // Additional video element events
      video.addEventListener('waiting', () => {
        this.log('Video waiting (stall detected)');
        this.sendPlayerEvent('stall_start', {
          current_time: video.currentTime,
          buffer_length: this.getBufferLength(video)
        });
      });

      video.addEventListener('playing', () => {
        this.log('Video playing (stall ended)');
        this.sendPlayerEvent('stall_end', {
          current_time: video.currentTime
        });
      });

      video.addEventListener('timeupdate', () => {
        // Send periodic heartbeat events (every 30 seconds)
        const now = Date.now();
        if (now - this.startTime > 30000 && (now - this.startTime) % 30000 < 1000) {
          this.sendPlayerEvent('heartbeat', {
            current_time: video.currentTime,
            buffer_length: this.getBufferLength(video),
            bitrate: this.bitrate,
            measured_throughput: this.measuredThroughput
          });
        }
      });

      hls.attachMedia(video);
      hls.on(Hls.Events.MEDIA_ATTACHED, () => {
        this.log('Media attached, loading source...');
        hls.loadSource(url);
      });

      this.hls = hls;

    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      this.log('Using native HLS support (Safari)');
      video.src = url;
    } else {
      this.log('HLS not supported in this browser');
      return;
    }

    // Start playback
    try {
      document.getElementById('play').disabled = true;
      await video.play();
      this.log('Playback started successfully');
      this.sendPlayerEvent('playback_start');
    } catch (e) {
      this.log(`Playback failed: ${e.message}`);
      this.sendPlayerEvent('playback_failed', { error: e.message });
    }

    // Send session analytics every 60 seconds
    this.analyticsInterval = setInterval(() => {
      this.sendSessionAnalytics();
    }, 60000);
  }

  getBufferLength(video) {
    try {
      if (video.buffered.length > 0) {
        return Math.round((video.buffered.end(video.buffered.length - 1) - video.currentTime) * 1000);
      }
    } catch (e) {
      return 0;
    }
    return 0;
  }

  sendSessionAnalytics() {
    const video = document.getElementById('video');
    const analytics = {
      session_duration: Date.now() - this.startTime,
      current_time: video.currentTime,
      buffer_health: this.getBufferLength(video),
      total_events: this.playerEvents.length,
      avg_throughput: this.measuredThroughput,
      current_bitrate: this.bitrate
    };

    this.log(`Session analytics: ${JSON.stringify(analytics)}`);
    this.sendPlayerEvent('session_analytics', analytics);
  }

  stop() {
    if (this.hls) {
      this.hls.destroy();
    }
    if (this.analyticsInterval) {
      clearInterval(this.analyticsInterval);
    }
    this.sendPlayerEvent('playback_stop');
    this.log('Playback stopped');
  }
}

// Global player instance
let player = null;

// Enhanced start function
async function start() {
  if (player) {
    player.stop();
  }
  
  player = new ViewbotGuardPlayer();
  await player.start();
}

// UI event handlers
document.getElementById('play').addEventListener('click', start);

// Add stop button functionality if exists
const stopButton = document.getElementById('stop');
if (stopButton) {
  stopButton.addEventListener('click', () => {
    if (player) {
      player.stop();
      document.getElementById('play').disabled = false;
    }
  });
}

// Page unload cleanup
window.addEventListener('beforeunload', () => {
  if (player) {
    player.stop();
  }
});
