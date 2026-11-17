"""Tests for Utter's menu bar icon: shapes, states, animation timer, and the app wiring."""

import dataclasses
from pathlib import Path

import pytest

from engine.config import VocabularyConfig, load_config
from engine.models import SessionMode
from engine.session import SessionManager
from interface import menu_icon as mi
from interface.app import VoiceAssistantApp


# ---- Shapes -------------------------------------------------------------------------------

def _inside_canvas(rects):
    for x, y, w, h in rects:
        assert x >= 0 and y >= 0 and x + w <= mi.ICON_W + 1e-9 and y + h <= mi.ICON_H + 1e-9


def test_ready_mark_is_a_symmetric_five_bar_waveform():
    rects = mi.bar_rects(mi.READY_BARS)
    assert len(rects) == 5
    _inside_canvas(rects)
    heights = [r[3] for r in rects]
    assert heights == heights[::-1] and max(heights) == heights[2] == mi.ICON_H
    # centred: equal space left and right
    assert rects[0][0] == pytest.approx(mi.ICON_W - (rects[-1][0] + rects[-1][2]))


def test_dictation_bars_follow_the_voice():
    for t in (0.0, 0.17, 0.5, 1.3, 7.9):
        quiet = mi.dictation_heights(0.0, t)
        loud = mi.dictation_heights(1.0, t)
        assert all(h == pytest.approx(mi.MIN_BAR) for h in quiet)  # silence: a row of dashes
        assert all(l > q for l, q in zip(loud, quiet))
        assert all(0.0 < h <= 1.0 for h in loud)
        _inside_canvas(mi.bar_rects(loud))
    # moving, not frozen, while you talk
    assert mi.dictation_heights(0.8, 0.0) != mi.dictation_heights(0.8, 0.2)


def test_orb_pulses_and_grows_a_little_with_the_voice():
    sizes = [mi.orb_scale(0.0, t / 10) for t in range(14)]
    assert max(sizes) - min(sizes) > 0.15
    assert all(0.5 < s <= 1.0 for s in sizes)
    assert mi.orb_scale(1.0, 0.0) > mi.orb_scale(0.0, 0.0)
    for s in (0.0, 0.62, 1.0, 3.0):
        _inside_canvas([mi.orb_rect(s)])


@pytest.mark.parametrize("dbfs, expected", [(-100, 0.0), (-55, 0.0), (-40, 0.5), (-25, 1.0), (0, 1.0)])
def test_microphone_loudness_maps_to_zero_to_one(dbfs, expected):
    assert mi.level_from_rms_dbfs(dbfs) == pytest.approx(expected)


def test_every_state_has_a_frame_and_loading_and_stopped_are_dimmed():
    assert mi.frame_for(mi.READY) == ("bars", mi.READY_BARS, 1.0)
    assert mi.frame_for(mi.LOADING) == ("bars", mi.READY_BARS, mi.DIM_ALPHA)
    assert mi.frame_for(mi.STOPPED) == ("bars", mi.READY_BARS, mi.DIM_ALPHA)
    assert mi.frame_for(mi.COMMAND, 0.5, 0.3)[0] == "orb"
    assert mi.frame_for(mi.DICTATION, 0.5, 0.3)[0] == "bars"


def test_rendered_icon_is_a_retina_template_image_of_fixed_size():
    for frame in (mi.frame_for(mi.READY), mi.frame_for(mi.COMMAND, 0.4, 0.2), mi.frame_for(mi.DICTATION, 1.0, 0.1)):
        image = mi.render_icon(frame)
        assert image.isTemplate()
        assert tuple(image.size()) == (mi.ICON_W, mi.ICON_H)  # same width in every state
        rep = image.representations()[0]
        assert (rep.pixelsWide(), rep.pixelsHigh()) == (int(mi.ICON_W * 2), int(mi.ICON_H * 2))


# ---- Controller ---------------------------------------------------------------------------

class FakeTimer:
    def __init__(self, callback, interval):
        self.callback, self.interval, self.running = callback, interval, False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _controller(level=0.0):
    shown, timers, clock = [], [], Clock()
    state = {"level": level}

    def make_timer(cb, interval):
        timers.append(FakeTimer(cb, interval))
        return timers[-1]

    icon = mi.MenuBarIcon(show=shown.append, level=lambda: state["level"], render=lambda f: f,
                          clock=clock, make_timer=make_timer)
    return icon, shown, timers, clock, state


def test_still_states_draw_once_and_never_run_a_timer():
    icon, shown, timers, _, _ = _controller()
    icon.set_state(mi.READY)
    icon.set_state(mi.READY)
    icon.tick()
    assert shown == [mi.frame_for(mi.READY)]  # nothing redrawn when nothing changed
    assert timers == [] and not icon.animating
    icon.set_state(mi.STOPPED)
    assert shown[-1] == mi.frame_for(mi.STOPPED)


def test_listening_animates_only_while_listening():
    icon, shown, timers, clock, state = _controller(level=0.9)
    icon.set_state(mi.DICTATION)
    assert icon.animating and timers[0].running
    assert timers[0].interval == pytest.approx(1 / mi.FRAMES_PER_SEC)
    first = shown[-1]
    clock.t += 0.2
    timers[0].callback()
    assert shown[-1] != first and shown[-1][0] == "bars"

    icon.set_state(mi.READY)  # you stopped speaking: straight back, no "transcribing" icon
    assert not icon.animating and not timers[0].running
    assert shown[-1] == mi.frame_for(mi.READY)


def test_switching_command_to_dictation_keeps_one_timer():
    icon, shown, timers, _, _ = _controller()
    icon.set_state(mi.COMMAND)
    assert shown[-1][0] == "orb"
    icon.set_state(mi.DICTATION)
    assert len(timers) == 1 and timers[0].running
    assert shown[-1][0] == "bars"


def test_voice_rises_at_once_and_falls_back_gently():
    icon, shown, timers, clock, state = _controller(level=1.0)
    icon.set_state(mi.DICTATION)
    loud = sum(shown[-1][1])
    state["level"] = 0.0
    clock.t += 0.07
    icon.tick()
    after_one_frame = sum(shown[-1][1])
    assert after_one_frame > 5 * mi.MIN_BAR + 0.5  # not an instant drop to dashes
    for _ in range(40):
        clock.t += 0.07
        icon.tick()
    assert sum(shown[-1][1]) == pytest.approx(5 * mi.MIN_BAR, abs=0.01) and loud > after_one_frame


def test_a_broken_level_reading_never_stops_the_icon():
    def boom():
        raise RuntimeError("mic gone")
    shown = []
    icon = mi.MenuBarIcon(show=shown.append, level=boom, render=lambda f: f, make_timer=FakeTimer)
    icon.set_state(mi.DICTATION)
    assert shown and shown[-1][0] == "bars"


def test_unknown_state_is_rejected():
    icon, *_ = _controller()
    with pytest.raises(ValueError):
        icon.set_state("transcribing")


# ---- App wiring ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    config = dataclasses.replace(load_config(), vocabulary=VocabularyConfig(file=None))
    a = VoiceAssistantApp(config=config, session_manager=SessionManager(config=config))
    a.overlay = None
    yield a
    a.menu_icon.stop()


def test_app_is_called_utter_and_shows_only_an_icon(app):
    assert app.name == "Utter"
    assert app.title is None
    assert app._icon_nsimage is not None and app._icon_nsimage.isTemplate()
    assert app.menu_icon.state == mi.READY  # no transcriber: ready at once


def test_app_menu_bar_follows_sessions_and_stop(app):
    app._on_session_start(SessionMode.COMMAND)
    assert app.menu_icon.state == mi.COMMAND and app.menu_icon.animating
    app._restore_idle_ui()
    assert app.menu_icon.state == mi.READY and not app.menu_icon.animating

    app._on_session_start(SessionMode.DICTATION)
    assert app.menu_icon.state == mi.DICTATION
    app._restore_idle_ui()

    app.toggle_service(None)
    assert app.menu_icon.state == mi.STOPPED and app.toggle_item.title == "Start Utter"
    app.toggle_service(None)
    assert app.menu_icon.state == mi.READY and app.toggle_item.title == "Stop Utter"


def test_session_reports_microphone_loudness_only_while_listening(app):
    import numpy as np
    sm = app.session_manager
    assert sm.input_rms_dbfs == -100.0
    sm._is_active = True  # as during a session; frames arrive from the audio thread
    sm._current_mode = SessionMode.DICTATION
    sm._on_audio_chunk(np.full(512, 0.1, dtype=np.float32))
    assert sm.input_rms_dbfs == pytest.approx(-20.0, abs=0.1)
    assert mi.level_from_rms_dbfs(sm.input_rms_dbfs) == 1.0
    sm._is_active = False
    assert sm.input_rms_dbfs == -100.0  # idle: no stale level


# ---- The pop-up: a moving sound wave while listening, nothing afterwards ------------------

def _overlay_spy(monkeypatch):
    from interface.overlay import VoiceAssistantOverlay
    overlay = VoiceAssistantOverlay()
    events = []
    monkeypatch.setattr(overlay, "_show_content", lambda text, auto_dismiss_sec=None: events.append(("text", text)))
    monkeypatch.setattr(overlay, "_show_wave", lambda: events.append(("wave",)))
    original_hide = overlay.hide
    monkeypatch.setattr(overlay, "hide", lambda: (events.append(("hide",)), original_hide()))
    return overlay, events


def test_listening_shows_only_the_wave_never_text(monkeypatch):
    overlay, events = _overlay_spy(monkeypatch)
    overlay.show_listening("command")
    overlay.show_listening("dictation")
    overlay.show_note_listening()
    assert events == [("wave",), ("wave",), ("wave",)]


@pytest.mark.parametrize("call", [
    lambda o: o.show_transcribing(),
    lambda o: o.show_dictation_inserted(4),
    lambda o: o.show_action_start("open notes", "Opening Notes..."),
    lambda o: o.show_action_success("open notes", "Opening Notes"),
    lambda o: o.show_saving_note(),
    lambda o: o.show_note_saved(12),
    lambda o: o.show_whatsapp_opening(),
    lambda o: o.show_whatsapp_ready(),
    lambda o: o.show_whatsapp_searching("Alex Test"),
])
def test_progress_and_success_messages_are_gone(monkeypatch, call):
    overlay, events = _overlay_spy(monkeypatch)
    call(overlay)
    assert events == [("hide",)]  # the pop-up just goes away


@pytest.mark.parametrize("call, expected", [
    (lambda o: o.show_not_run('No app called "door"'), 'No app called "door"'),
    (lambda o: o.show_unrecognized("open the door"), "Nothing recognized"),
    (lambda o: o.show_error("boom"), "Action failed"),
    (lambda o: o.show_note_failed_copied("permission denied"), "Text copied to clipboard"),
    (lambda o: o.show_whatsapp_not_front(), "WhatsApp isn't in front"),
])
def test_problems_are_still_shown_as_text(monkeypatch, call, expected):
    overlay, events = _overlay_spy(monkeypatch)
    call(overlay)
    assert len(events) == 1 and events[0][0] == "text" and expected in events[0][1]


def test_wave_scrolls_with_the_voice_and_stays_small_in_silence():
    import random
    from interface import waveform as wf
    wave = wf.WaveState(rng=random.Random(7))
    quiet = wave.step(0.0)
    assert len(quiet) == wf.BAR_COUNT and max(quiet) <= wf.IDLE  # silence: only small twitches
    for _ in range(wf.BAR_COUNT):
        loud = wave.step(1.0)
    assert min(loud) > 0.05 and max(loud) <= 1.0
    assert wf.taper(wf.BAR_COUNT // 2) > wf.taper(0)  # tall in the middle, short at the ends
    newest = wave.history[-1]
    wave.step(1.0)
    assert wave.history[-2] == newest  # scrolls: each frame pushes the wave one bar left
    for _ in range(30):
        rest = wave.step(0.0)
    assert max(rest) <= wf.IDLE  # back to small twitches quickly when you stop talking
    for x, y, w, h in wf.bar_rects(loud):
        assert x >= 0 and y >= 0 and x + w <= wf.WIDTH + 1e-9 and y + h <= wf.HEIGHT + 1e-9
        assert w == wf.BAR_W == 3.0  # bold bars


def test_wave_is_lively_not_smooth():
    import random
    import statistics
    from interface import waveform as wf
    wave = wf.WaveState(rng=random.Random(11))
    frames = [wave.step(0.5) for _ in range(48)][wf.BAR_COUNT:]
    middle = [f[wf.BAR_COUNT // 2] for f in frames]
    jumps = [abs(a - b) for a, b in zip(middle, middle[1:])]
    assert statistics.mean(jumps) > 0.12  # the middle bar jumps a lot from frame to frame
    assert statistics.pstdev(frames[-1]) > 0.08  # neighbouring bars differ clearly
    # quiet speech still makes clearly visible bars
    soft = wf.WaveState(rng=random.Random(3))
    for _ in range(wf.BAR_COUNT):
        bars = soft.step(0.3)  # soft speech, about -46 dBFS
    assert max(bars) * wf.MAX_BAR > 2 * wf.MIN_BAR
    # it calms down fast once you stop: under a third of a second
    stop = wf.WaveState(rng=random.Random(5))
    stop.step(1.0)
    for _ in range(int(wf.FRAMES_PER_SEC / 3)):
        stop.step(0.0)
    assert stop.smoothed < 0.01


def test_wave_view_draws_without_errors():
    import AppKit
    from interface import waveform as wf
    from interface.overlay import UtterWaveView
    view = UtterWaveView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, wf.WIDTH, wf.HEIGHT))
    view.heights = [0.5] * wf.BAR_COUNT
    image = AppKit.NSImage.alloc().initWithSize_((wf.WIDTH, wf.HEIGHT))
    image.lockFocus()
    try:
        view.drawRect_(view.bounds())
    finally:
        image.unlockFocus()


def test_app_feeds_the_microphone_level_to_the_wave(tmp_path):
    import dataclasses
    from engine.config import VocabularyConfig, load_config
    from engine.session import SessionManager
    from interface.app import VoiceAssistantApp
    config = dataclasses.replace(load_config(), vocabulary=VocabularyConfig(file=None))
    a = VoiceAssistantApp(config=config, session_manager=SessionManager(config=config))
    try:
        assert a.overlay is not None and a.overlay.level_source() == 0.0  # idle: silent
        a.session_manager._is_active = True
        a.session_manager._input_rms_dbfs = -25.0
        assert a.overlay.level_source() == 1.0
        a.session_manager._is_active = False
    finally:
        a.menu_icon.stop()
