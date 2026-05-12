(function () {
  const peers = new Map();
  let loggingState = {
    active: false,
    session_id: "",
  };

  function waitForIceGatheringComplete(pc, timeoutMs) {
    if (pc.iceGatheringState === "complete") {
      return Promise.resolve();
    }

    return new Promise((resolve) => {
      const timer = window.setTimeout(() => {
        pc.removeEventListener("icegatheringstatechange", onStateChange);
        resolve();
      }, timeoutMs);

      function onStateChange() {
        if (pc.iceGatheringState === "complete") {
          window.clearTimeout(timer);
          pc.removeEventListener("icegatheringstatechange", onStateChange);
          resolve();
        }
      }

      pc.addEventListener("icegatheringstatechange", onStateChange);
    });
  }

  function updateState(source, text) {
    const stateNode = document.querySelector(`[data-webrtc-state="${source}"]`);
    if (stateNode) {
      stateNode.textContent = text;
    }
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error || body.message || `Request failed (${response.status})`);
    }
    return body;
  }

  async function requestOfferRestart(source) {
    await fetchJson(`/api/webrtc/${encodeURIComponent(source)}/restart`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  }

  function pickRecordingMimeType() {
    const candidates = [
      "video/webm;codecs=vp9",
      "video/webm;codecs=vp8",
      "video/webm",
    ];

    for (const mimeType of candidates) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(mimeType)) {
        return mimeType;
      }
    }
    return "";
  }

  async function refreshLoggingStatus() {
    try {
      loggingState = await fetchJson("/api/logging/status", { cache: "no-store" });
    } catch (error) {
      console.warn("Logging status poll failed", error);
    }
  }

  async function uploadVideoChunk(entry, blob) {
    if (!blob || !blob.size || !entry.recordingSessionId) {
      return;
    }

    const response = await fetch(`/api/logging/video_chunk/${encodeURIComponent(entry.source)}`, {
      method: "POST",
      headers: {
        "Content-Type": blob.type || "application/octet-stream",
        "X-Session-Id": entry.recordingSessionId,
        "X-Video-Mime-Type": entry.recordingMimeType || blob.type || "video/webm",
      },
      body: blob,
    });

    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error || `Video upload failed (${response.status})`);
    }
  }

  async function flushFrameEvents(entry) {
    if (!entry.frameEvents.length || !entry.recordingSessionId || entry.frameUploadInFlight) {
      return;
    }

    const frames = entry.frameEvents.splice(0, entry.frameEvents.length);
    entry.frameUploadInFlight = true;
    try {
      const response = await fetch(`/api/logging/video_frames/${encodeURIComponent(entry.source)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: entry.recordingSessionId,
          frames,
        }),
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || `Frame timestamp upload failed (${response.status})`);
      }
    } catch (error) {
      entry.frameEvents.unshift(...frames);
      throw error;
    } finally {
      entry.frameUploadInFlight = false;
    }
  }

  function scheduleFrameTracking(entry) {
    if (!entry.video || !entry.video.requestVideoFrameCallback || !entry.recordingActive) {
      return;
    }

    const activeSessionId = entry.recordingSessionId;
    const onFrame = (_now, metadata) => {
      if (!entry.recordingActive || entry.recordingSessionId !== activeSessionId) {
        return;
      }

      entry.frameEvents.push({
        captured_at_ms: Date.now(),
        media_time_s: metadata.mediaTime,
        presented_frames: metadata.presentedFrames,
        width: metadata.width,
        height: metadata.height,
      });

      if (entry.frameEvents.length >= 30) {
        flushFrameEvents(entry).catch((error) => {
          console.warn(`Frame timestamp upload failed for ${entry.source}`, error);
        });
      }

      entry.video.requestVideoFrameCallback(onFrame);
    };

    entry.video.requestVideoFrameCallback(onFrame);
  }

  function stopRecording(entry) {
    entry.recordingActive = false;
    if (entry.recorder && entry.recorder.state !== "inactive") {
      try {
        entry.recorder.stop();
      } catch (error) {
        console.warn(`Failed to stop recorder for ${entry.source}`, error);
      }
    } else {
      entry.recorder = null;
      entry.recordingSessionId = "";
      entry.recordingMimeType = "";
      entry.frameEvents = [];
    }
  }

  function startRecording(entry) {
    if (!loggingState.active || !loggingState.session_id || !entry.stream || !window.MediaRecorder) {
      return;
    }
    if (entry.recorder && entry.recordingSessionId === loggingState.session_id) {
      return;
    }

    const mimeType = pickRecordingMimeType();
    let recorder;
    try {
      recorder = mimeType
        ? new MediaRecorder(entry.stream, { mimeType })
        : new MediaRecorder(entry.stream);
    } catch (error) {
      console.warn(`Unable to start video recorder for ${entry.source}`, error);
      return;
    }

    entry.recorder = recorder;
    entry.recordingActive = true;
    entry.recordingSessionId = loggingState.session_id;
    entry.recordingMimeType = mimeType || recorder.mimeType || "video/webm";
    entry.frameEvents = [];
    entry.frameUploadInFlight = false;

    recorder.addEventListener("dataavailable", (event) => {
      uploadVideoChunk(entry, event.data).catch((error) => {
        console.warn(`Video upload failed for ${entry.source}`, error);
      });
    });

    recorder.addEventListener("stop", () => {
      flushFrameEvents(entry).catch((error) => {
        console.warn(`Frame flush failed for ${entry.source}`, error);
      });
      entry.recorder = null;
      entry.recordingActive = false;
      entry.recordingSessionId = "";
      entry.recordingMimeType = "";
      if (loggingState.active) {
        startRecording(entry);
      }
    });

    recorder.start(1000);
    scheduleFrameTracking(entry);
  }

  function syncRecording(entry) {
    if (!entry) {
      return;
    }
    if (!loggingState.active) {
      stopRecording(entry);
      return;
    }
    if (entry.recorder && entry.recordingSessionId !== loggingState.session_id) {
      stopRecording(entry);
      return;
    }
    if (!entry.recorder) {
      startRecording(entry);
    }
  }

  async function connectSource(source, video) {
    let entry = peers.get(source);
    if (!entry) {
      entry = {
        source,
        video,
        pc: null,
        stream: null,
        signalId: "",
        answeredSignalId: "",
        inFlight: false,
        lastRestartAt: 0,
        recorder: null,
        recordingActive: false,
        recordingSessionId: "",
        recordingMimeType: "",
        frameEvents: [],
        frameUploadInFlight: false,
      };
      peers.set(source, entry);
    } else {
      entry.video = video;
      if (entry.stream && entry.video.srcObject !== entry.stream) {
        entry.video.srcObject = entry.stream;
      }
    }

    if (entry.inFlight) {
      return entry;
    }

    if (
      entry.pc &&
      entry.signalId &&
      !["failed", "disconnected", "closed"].includes(entry.pc.connectionState)
    ) {
      syncRecording(entry);
      return entry;
    }

    entry.inFlight = true;
    try {
      const offer = await fetchJson(`/api/webrtc/${encodeURIComponent(source)}/offer`, {
        cache: "no-store",
      });

      if (!offer.available) {
        updateState(source, offer.message || "Waiting for WebRTC offer.");
        if (Date.now() - entry.lastRestartAt > 3000) {
          entry.lastRestartAt = Date.now();
          await requestOfferRestart(source);
          updateState(source, "Requested a fresh WebRTC offer.");
        }
        return;
      }

      if (
        offer.signal_id &&
        entry.answeredSignalId &&
        offer.signal_id === entry.answeredSignalId &&
        (!entry.pc || ["failed", "disconnected", "closed"].includes(entry.pc.connectionState))
      ) {
        updateState(source, "Requesting a fresh WebRTC offer...");
        if (Date.now() - entry.lastRestartAt > 3000) {
          entry.lastRestartAt = Date.now();
          await requestOfferRestart(source);
        }
        return;
      }

      if (entry.pc) {
        try {
          entry.pc.close();
        } catch (error) {
          console.warn("Failed to close existing peer connection", error);
        }
      }

      const pc = new RTCPeerConnection({ iceServers: [] });
      entry.pc = pc;
      entry.signalId = offer.signal_id || "";

      pc.addEventListener("track", (event) => {
        const [stream] = event.streams;
        if (stream) {
          entry.stream = stream;
          entry.video.srcObject = stream;
        } else {
          const fallbackStream = new MediaStream([event.track]);
          entry.stream = fallbackStream;
          entry.video.srcObject = fallbackStream;
        }
        syncRecording(entry);
      });

      pc.addEventListener("connectionstatechange", () => {
        updateState(source, `WebRTC ${pc.connectionState}`);
        if (["failed", "disconnected", "closed"].includes(pc.connectionState)) {
          entry.signalId = "";
          stopRecording(entry);
        }
      });

      updateState(source, "Applying WebRTC offer...");
      await pc.setRemoteDescription({
        type: offer.sdp_type || "offer",
        sdp: offer.sdp,
      });

      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      await waitForIceGatheringComplete(pc, 5000);

      await fetchJson(`/api/webrtc/${encodeURIComponent(source)}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          signal_id: offer.signal_id || "",
          sdp_type: pc.localDescription.type,
          sdp: pc.localDescription.sdp,
        }),
      });

      entry.answeredSignalId = offer.signal_id || "";
      updateState(source, "WebRTC answer sent. Waiting for media...");
    } catch (error) {
      updateState(source, `WebRTC error: ${error.message}`);
      if (entry.pc) {
        try {
          entry.pc.close();
        } catch (closeError) {
          console.warn("Failed to close peer after WebRTC error", closeError);
        }
        entry.pc = null;
      }
      entry.signalId = "";
    } finally {
      entry.inFlight = false;
    }
    syncRecording(entry);
    return entry;
  }

  async function scanCameraPanels() {
    const panels = document.querySelectorAll("[data-webrtc-source]");
    for (const panel of panels) {
      const source = panel.getAttribute("data-webrtc-source");
      const video = panel.querySelector("video[data-webrtc-video]");
      if (!source || !video) {
        continue;
      }
      const entry = await connectSource(source, video);
      syncRecording(entry);
    }
  }

  function startLoop() {
    refreshLoggingStatus();
    scanCameraPanels().catch((error) => {
      console.error("WebRTC camera scan failed", error);
    });
    window.setInterval(() => {
      refreshLoggingStatus().then(() => {
        for (const entry of peers.values()) {
          syncRecording(entry);
        }
      });
    }, 1000);
    window.setInterval(() => {
      scanCameraPanels().catch((error) => {
        console.error("WebRTC camera polling failed", error);
      });
    }, 2500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startLoop, { once: true });
  } else {
    startLoop();
  }
})();
