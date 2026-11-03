-- WangertTV Player – Bedienoberfläche für die Fernbedienung (läuft in mpv)
--
-- OK            Pause / Weiter (zeigt die Leiste)
-- Links/Rechts  Spulen (gedrückt halten = schneller); bei Bildern: vorheriges/nächstes Bild
-- Hoch          Menü: Tonspur, Untertitel, Geschwindigkeit, Bildformat, Kapitel, Wiedergabeliste, Verzögerungen …
-- Runter        Infos (Format, Auflösung, Ton, Hardware-Dekodierung)
-- Zurück        Menü schließen bzw. Wiedergabe beenden (Position wird gemerkt)
-- 0–9           an 0 % … 90 % springen
-- Kanal +/-, ⏭/⏮ nächste / vorherige Datei
-- Rot / Grün / Gelb / Blau   Tonspur / Untertitel / Geschwindigkeit / Infos

local mp = require "mp"
local utils = require "mp.utils"
local msg = require "mp.msg"

local W, H = 1920, 1080
local ACCENT = "FFE122"          -- #22e1ff (ASS-Farben sind BGR)
local WHITE = "FFFFFF"
local MUTED = "C9B6A6"           -- #a6b6c9
local FONT = "Inter"
local LANG = os.getenv("WTV_LANG") or "de"
local PROGRESS_FILE = os.getenv("WTV_PLAYER_PROGRESS") or ""
local skip_resume = os.getenv("WTV_START_OVER") == "1"   -- "Von vorn": erste Datei nicht fortsetzen

local T = {
  de = {audio = "Tonspur", subs = "Untertitel", speed = "Geschwindigkeit", aspect = "Bildformat", zoom = "Zoom",
        chapters = "Kapitel", playlist = "Wiedergabeliste", subdelay = "Untertitel-Verzögerung",
        audiodelay = "Ton-Verzögerung", loop = "Wiederholen", info = "Infos", off = "Aus", on = "An",
        auto = "Automatisch", none = "Keine", menu = "Menü", back = "Zurück", paused = "Pause",
        loop_file = "Diese Datei", loop_list = "Ganze Liste", hint = "▲ Menü   ▼ Infos   OK Pause   ◀ ▶ Spulen",
        image = "Bild", of = "von", ends = "Ende um", adjust = "◀ ▶ zum Einstellen", stats_video = "Bild",
        stats_audio = "Ton", stats_hw = "Hardware-Dekodierung", stats_file = "Datei", passthrough = "durchgereicht an den Receiver",
        resumed = "Fortgesetzt bei", default = "Standard", fill = "Bild füllen", original = "Original"},
  en = {audio = "Audio track", subs = "Subtitles", speed = "Speed", aspect = "Aspect ratio", zoom = "Zoom",
        chapters = "Chapters", playlist = "Playlist", subdelay = "Subtitle delay", audiodelay = "Audio delay",
        loop = "Repeat", info = "Info", off = "Off", on = "On", auto = "Automatic", none = "None", menu = "Menu",
        back = "Back", paused = "Paused", loop_file = "This file", loop_list = "Whole list",
        hint = "▲ Menu   ▼ Info   OK Pause   ◀ ▶ Seek", image = "Image", of = "of", ends = "Ends at",
        adjust = "◀ ▶ to adjust", stats_video = "Video", stats_audio = "Audio", stats_hw = "Hardware decoding",
        stats_file = "File", passthrough = "passed through to the receiver", resumed = "Resumed at",
        default = "Default", fill = "Fill screen", original = "Original"},
}
local L = T[LANG] or T.de

local overlay = mp.create_osd_overlay("ass-events")
overlay.res_x, overlay.res_y = W, H

local state = {
  osd_until = 0,        -- Leiste sichtbar bis (Zeit)
  info = false,         -- Infofenster
  menu = nil,           -- {title, items, sel, parent, kind}
  flash = nil,          -- {text, until}
  seek_held = nil,      -- {dir, since}
}

-- ------------------------------------------------------------------ Hilfen
local function esc(s)
  s = tostring(s or "")
  s = s:gsub("\\", "\\\226\129\160"):gsub("{", "\\{"):gsub("}", "\\}"):gsub("\n", " ")
  return s
end

local function fmt_time(t)
  if not t or t < 0 then return "--:--" end
  t = math.floor(t + 0.5)
  local h, m, s = math.floor(t / 3600), math.floor(t / 60) % 60, t % 60
  if h > 0 then return string.format("%d:%02d:%02d", h, m, s) end
  return string.format("%d:%02d", m, s)
end

local function now() return mp.get_time() end

local function is_image()
  local tr = mp.get_property_native("current-tracks/video")
  return tr and tr.image and not tr.albumart
end

local function is_audio_only()
  local tr = mp.get_property_native("current-tracks/video")
  return not tr or tr.albumart
end

local function box(x, y, w, h, color, alpha, radius)
  radius = radius or 0
  local r = math.min(radius, h / 2, w / 2)
  local path
  if r > 0 then
    -- abgerundetes Rechteck (Bezier-Ecken)
    local k = r * 0.45
    path = string.format(
      "m %d 0 l %d 0 b %d 0 %d %d %d %d l %d %d b %d %d %d %d %d %d l %d %d b %d %d 0 %d 0 %d l 0 %d b 0 %d %d 0 %d 0",
      r, w - r, w - r + k, w, r - k, w, r, w, h - r, w, h - r + k, w - r + k, h, w - r, h, r, h, r - k, h, h - r + k,
      h - r, r, r - k, k, r)
  else
    path = string.format("m 0 0 l %d 0 l %d %d l 0 %d", w, w, h, h)
  end
  return string.format("{\\an7\\pos(%d,%d)\\bord0\\shad0\\1c&H%s&\\1a&H%s&\\p1}%s{\\p0}", x, y, color, alpha or "00", path)
end

local function text(x, y, s, size, color, align, bold, alpha)
  return string.format("{\\an%d\\pos(%d,%d)\\fn%s\\fs%d\\b%d\\bord0\\shad1\\4a&H90&\\1c&H%s&\\1a&H%s&}%s",
    align or 7, x, y, FONT, size, bold and 1 or 0, color or WHITE, alpha or "00", s)
end

-- ------------------------------------------------------------------ Darstellung
local function render_bar(lines)
  local pos = mp.get_property_number("time-pos")
  local dur = mp.get_property_number("duration")
  local paused = mp.get_property_bool("pause")
  local title = mp.get_property("media-title") or ""
  local artist = mp.get_property("metadata/by-key/Artist") or mp.get_property("metadata/by-key/ARTIST")
  local album = mp.get_property("metadata/by-key/Album") or mp.get_property("metadata/by-key/ALBUM")
  local pl_pos, pl_count = mp.get_property_number("playlist-pos-1"), mp.get_property_number("playlist-count")

  -- dunkler Verlauf unten und oben
  lines[#lines + 1] = box(0, H - 330, W, 330, "000000", "50")
  lines[#lines + 1] = box(0, 0, W, 150, "000000", "70")

  -- oben: Uhrzeit und Position in der Liste
  lines[#lines + 1] = text(W - 80, 52, os.date("%H:%M"), 46, WHITE, 9, true)
  if pl_count and pl_count > 1 then
    lines[#lines + 1] = text(80, 58, string.format("%d %s %d", pl_pos or 1, L.of, pl_count), 30, MUTED, 7)
  end

  -- Titel (+ Interpret/Album bei Musik)
  local sub = {}
  if artist then sub[#sub + 1] = artist end
  if album then sub[#sub + 1] = album end
  lines[#lines + 1] = text(80, H - 290, esc(title), 54, WHITE, 7, true)
  if #sub > 0 then lines[#lines + 1] = text(80, H - 222, esc(table.concat(sub, " · ")), 32, MUTED, 7) end

  if is_image() then
    lines[#lines + 1] = text(80, H - 150, L.image .. "   ◀ ▶", 30, MUTED, 7)
    return
  end

  -- Fortschrittsbalken
  local bx, by, bw, bh = 80, H - 150, W - 160, 12
  lines[#lines + 1] = box(bx, by, bw, bh, "FFFFFF", "C8", 6)
  if dur and dur > 0 and pos then
    local cache = mp.get_property_number("demuxer-cache-time")
    if cache and cache > pos then
      local cw = math.floor(bw * math.min(1, cache / dur))
      lines[#lines + 1] = box(bx, by, cw, bh, "FFFFFF", "A0", 6)
    end
    local fw = math.max(bh, math.floor(bw * math.min(1, pos / dur)))
    lines[#lines + 1] = box(bx, by, fw, bh, ACCENT, "00", 6)
    lines[#lines + 1] = box(bx + fw - 14, by - 8, 28, 28, WHITE, "00", 14)
    -- Kapitelmarken
    local chapters = mp.get_property_native("chapter-list") or {}
    for _, c in ipairs(chapters) do
      if c.time and c.time > 0 and c.time < dur then
        lines[#lines + 1] = box(bx + math.floor(bw * c.time / dur) - 2, by, 4, bh, "000000", "40")
      end
    end
    local left = dur - pos
    local speed = mp.get_property_number("speed") or 1
    lines[#lines + 1] = text(bx, by + 34, fmt_time(pos), 34, WHITE, 7, true)
    lines[#lines + 1] = text(bx + bw, by + 34, "-" .. fmt_time(left) .. "   " .. L.ends .. " " ..
      os.date("%H:%M", os.time() + math.floor(left / speed)), 34, MUTED, 9)
  elseif pos then
    lines[#lines + 1] = box(bx, by, bw, bh, ACCENT, "00", 6)        -- Live-Stream
    lines[#lines + 1] = text(bx, by + 34, "LIVE · " .. fmt_time(pos), 34, WHITE, 7, true)
  end

  -- Status und Hinweise
  local status = {}
  if paused then status[#status + 1] = "❚❚ " .. L.paused end
  local speed = mp.get_property_number("speed") or 1
  if math.abs(speed - 1) > 0.01 then status[#status + 1] = string.format("%.2gx", speed) end
  local loopf, loopl = mp.get_property("loop-file"), mp.get_property("loop-playlist")
  if (loopf and loopf ~= "no") or (loopl and loopl ~= "no") then status[#status + 1] = "⟳" end
  lines[#lines + 1] = text(W / 2, by + 34, table.concat(status, "    "), 34, ACCENT, 8, true)
  lines[#lines + 1] = text(W / 2, H - 38, L.hint, 26, MUTED, 2)
end

local function render_menu(lines)
  local m = state.menu
  local n = #m.items
  local x, w = W - 760, 680
  local rowh = 74
  local maxrows = 10
  local first = math.max(1, math.min(m.sel - math.floor(maxrows / 2), n - maxrows + 1))
  local shown = math.min(n, maxrows)
  local h = 140 + shown * rowh + (m.hint and 60 or 0)
  local y = math.floor((H - h) / 2)
  lines[#lines + 1] = box(x, y, w, h, "1A100B", "18", 36)     -- #0b101a
  lines[#lines + 1] = text(x + 44, y + 40, esc(m.title), 42, WHITE, 7, true)
  for i = first, math.min(n, first + maxrows - 1) do
    local it = m.items[i]
    local ry = y + 110 + (i - first) * rowh
    local selected = i == m.sel
    if selected then lines[#lines + 1] = box(x + 24, ry, w - 48, rowh - 8, "FFF5EC", "00", 22) end
    local fg = selected and "1A100B" or WHITE
    local mark = it.active and "●  " or "    "
    lines[#lines + 1] = text(x + 48, ry + (rowh - 8) / 2, mark .. esc(it.label), 34, fg, 4, selected)
    if it.value then
      lines[#lines + 1] = text(x + w - 48, ry + (rowh - 8) / 2, esc(it.value) .. (it.sub and "  ›" or ""), 30,
        selected and "4A3A2A" or MUTED, 6)
    elseif it.sub then
      lines[#lines + 1] = text(x + w - 48, ry + (rowh - 8) / 2, "›", 34, selected and "4A3A2A" or MUTED, 6)
    end
  end
  if m.hint then lines[#lines + 1] = text(x + w / 2, y + h - 40, m.hint, 26, MUTED, 5) end
end

local function stats_lines()
  local out = {}
  local v = mp.get_property_native("current-tracks/video")
  local a = mp.get_property_native("current-tracks/audio")
  local vp = mp.get_property_native("video-params") or {}
  if v and not v.albumart then
    out[#out + 1] = {L.stats_video, string.format("%s · %dx%d%s%s", (v.codec or "?"):upper(), vp.w or v["demux-w"] or 0,
      vp.h or v["demux-h"] or 0, v["demux-fps"] and string.format(" · %.3g fps", v["demux-fps"]) or "",
      vp.gamma == "pq" and " · HDR10" or vp.gamma == "hlg" and " · HLG" or "")}
    local hw = mp.get_property("hwdec-current")
    out[#out + 1] = {L.stats_hw, (hw and hw ~= "no" and hw ~= "") and (L.on .. " (" .. hw .. ")") or L.off}
  end
  if a then
    local codec = mp.get_property("audio-codec-name") or a.codec or "?"
    local ch = mp.get_property("audio-params/hr-channels") or a["demux-channels"] or ""
    local spdif = (mp.get_property("audio-out-params/format") or ""):find("spdif") ~= nil
    out[#out + 1] = {L.stats_audio, string.format("%s · %s%s", codec:upper(), ch, spdif and (" · " .. L.passthrough) or "")}
  end
  local size = mp.get_property_number("file-size")
  local br = mp.get_property_number("video-bitrate")
  out[#out + 1] = {L.stats_file, (mp.get_property("file-format") or "?") ..
    (size and string.format(" · %.1f GB", size / 1e9) or "") ..
    (br and string.format(" · %.1f Mbit/s", br / 1e6) or "")}
  return out
end

local function render_info(lines)
  local rows = stats_lines()
  local x, y, w = 80, 190, 1100
  local h = 90 + #rows * 66
  lines[#lines + 1] = box(x, y, w, h, "1A100B", "28", 32)
  lines[#lines + 1] = text(x + 40, y + 30, L.info, 38, WHITE, 7, true)
  for i, r in ipairs(rows) do
    lines[#lines + 1] = text(x + 40, y + 50 + i * 66, r[1], 28, MUTED, 4)
    lines[#lines + 1] = text(x + 360, y + 50 + i * 66, esc(r[2]), 30, WHITE, 4)
  end
end

local function render()
  local lines = {}
  local t = now()
  local show_bar = t < state.osd_until or mp.get_property_bool("pause") or state.menu ~= nil or state.info
  if is_audio_only() and not is_image() then show_bar = true end
  if show_bar then render_bar(lines) end
  if state.info then render_info(lines) end
  if state.menu then render_menu(lines) end
  if state.flash and t < state.flash.untill then
    local fw = math.min(W - 200, math.max(520, 34 * #state.flash.text))
    lines[#lines + 1] = box(W / 2 - fw / 2, H / 2 - 90, fw, 180, "000000", "50", 40)
    lines[#lines + 1] = text(W / 2, H / 2, esc(state.flash.text), 64, WHITE, 5, true)
  end
  overlay.data = table.concat(lines, "\n")
  overlay:update()
end

local function show_osd(seconds)
  state.osd_until = now() + (seconds or 4)
  render()
end

local function flash(s, seconds)
  state.flash = {text = s, untill = now() + (seconds or 1.1)}
  render()
end

-- ------------------------------------------------------------------ Menüs
local close_menu, open_menu

local function track_menu(kind)
  local items = {}
  local tracks = mp.get_property_native("track-list") or {}
  local current = mp.get_property(kind == "audio" and "aid" or "sid")
  if kind == "sub" then
    items[#items + 1] = {label = L.off, active = current == "no", run = function() mp.set_property("sid", "no") end}
  end
  for _, tr in ipairs(tracks) do
    if tr.type == kind then
      local parts = {}
      if tr.lang then parts[#parts + 1] = tr.lang:upper() end
      if tr.title then parts[#parts + 1] = tr.title end
      local detail = (tr.codec or "")
      if kind == "audio" and tr["demux-channel-count"] then detail = detail .. " " .. tr["demux-channel-count"] .. "ch" end
      if tr.forced then detail = detail .. " forced" end
      if tr.external then detail = detail .. " ext" end
      items[#items + 1] = {label = (#parts > 0 and table.concat(parts, " – ") or ("#" .. tr.id)), value = detail:upper(),
        active = tr.selected, run = function() mp.set_property_number(kind == "audio" and "aid" or "sid", tr.id) end}
    end
  end
  if #items == 0 then items[1] = {label = L.none} end
  return {title = kind == "audio" and L.audio or L.subs, items = items}
end

local function choice_menu(title, prop, choices)
  local cur = mp.get_property(prop)
  local items = {}
  for _, c in ipairs(choices) do
    items[#items + 1] = {label = c[2], active = tostring(cur) == tostring(c[1]) or
      (tonumber(cur) and tonumber(c[1]) and math.abs(tonumber(cur) - tonumber(c[1])) < 0.001),
      run = function() mp.set_property(prop, c[1]) end}
  end
  return {title = title, items = items}
end

local function adjust_menu(title, prop, step, unit)
  return {title = title, kind = "adjust", prop = prop, step = step, unit = unit, hint = L.adjust,
    items = {{label = title, value = string.format("%+.1f %s", mp.get_property_number(prop) or 0, unit)},
             {label = L.default, run = function() mp.set_property_number(prop, 0) end}}}
end

local function chapter_menu()
  local items = {}
  local cur = mp.get_property_number("chapter")
  for i, c in ipairs(mp.get_property_native("chapter-list") or {}) do
    items[#items + 1] = {label = c.title and c.title ~= "" and c.title or (L.chapters .. " " .. i), value = fmt_time(c.time),
      active = cur == i - 1, run = function() mp.set_property_number("chapter", i - 1) end}
  end
  if #items == 0 then items[1] = {label = L.none} end
  return {title = L.chapters, items = items}
end

local function playlist_menu()
  local items = {}
  for i, e in ipairs(mp.get_property_native("playlist") or {}) do
    local name = e.title or (e.filename or ""):match("([^/]+)$") or e.filename
    items[#items + 1] = {label = name, active = e.current, run = function() mp.set_property_number("playlist-pos", i - 1) end}
  end
  return {title = L.playlist, items = items}
end

local function main_menu()
  local aid = mp.get_property_native("current-tracks/audio")
  local sid = mp.get_property_native("current-tracks/sub")
  local items = {
    {label = L.audio, value = aid and ((aid.lang or ""):upper() .. " " .. (aid.codec or ""):upper()) or L.none, sub = function() return track_menu("audio") end},
    {label = L.subs, value = sid and ((sid.lang or sid.title or "#" .. sid.id):upper()) or L.off, sub = function() return track_menu("sub") end},
    {label = L.speed, value = string.format("%.2gx", mp.get_property_number("speed") or 1), sub = function()
      return choice_menu(L.speed, "speed", {{"0.5", "0,5x"}, {"0.75", "0,75x"}, {"1", "1x"}, {"1.25", "1,25x"}, {"1.5", "1,5x"}, {"2", "2x"}}) end},
    {label = L.aspect, sub = function()
      return choice_menu(L.aspect, "video-aspect-override", {{"-1", L.original}, {"16:9", "16:9"}, {"4:3", "4:3"},
        {"2.35:1", "21:9 (2,35:1)"}, {"1.85:1", "1,85:1"}}) end},
    {label = L.zoom, sub = function()
      return choice_menu(L.zoom, "panscan", {{"0", L.original}, {"1", L.fill}}) end},
    {label = L.subdelay, value = string.format("%+.1f s", mp.get_property_number("sub-delay") or 0),
      sub = function() return adjust_menu(L.subdelay, "sub-delay", 0.1, "s") end},
    {label = L.audiodelay, value = string.format("%+.1f s", mp.get_property_number("audio-delay") or 0),
      sub = function() return adjust_menu(L.audiodelay, "audio-delay", 0.05, "s") end},
  }
  if #(mp.get_property_native("chapter-list") or {}) > 0 then
    items[#items + 1] = {label = L.chapters, sub = chapter_menu}
  end
  if (mp.get_property_number("playlist-count") or 1) > 1 then
    items[#items + 1] = {label = L.playlist, sub = playlist_menu}
  end
  local lf, lp = mp.get_property("loop-file"), mp.get_property("loop-playlist")
  items[#items + 1] = {label = L.loop, value = (lf ~= "no" and L.loop_file) or (lp ~= "no" and L.loop_list) or L.off,
    run = function()
      if lf ~= "no" then mp.set_property("loop-file", "no"); mp.set_property("loop-playlist", "inf")
      elseif lp ~= "no" then mp.set_property("loop-playlist", "no")
      else mp.set_property("loop-file", "inf") end
      open_menu(main_menu(), true)
    end}
  items[#items + 1] = {label = L.info, run = function() close_menu(); state.info = true; show_osd(8) end}
  return {title = L.menu, items = items}
end

open_menu = function(m, keep_sel)
  local old = state.menu
  if keep_sel and old then m.sel = old.sel; m.parent = old.parent end
  m.sel = m.sel or 1
  for i, it in ipairs(m.items) do if it.active and not keep_sel then m.sel = i end end
  if not keep_sel then m.parent = old end
  state.menu = m
  state.info = false
  render()
end

close_menu = function()
  state.menu = nil
  show_osd(3)
end

local function menu_key(key)
  local m = state.menu
  if key == "up" then m.sel = (m.sel - 2) % #m.items + 1
  elseif key == "down" then m.sel = m.sel % #m.items + 1
  elseif key == "back" then
    if m.parent then state.menu = m.parent; render() else close_menu() end
    return
  elseif (key == "left" or key == "right") and m.kind == "adjust" then
    local v = (mp.get_property_number(m.prop) or 0) + (key == "right" and m.step or -m.step)
    mp.set_property_number(m.prop, v)
    m.items[1].value = string.format("%+.2f %s", v, m.unit)
  elseif key == "ok" or key == "right" then
    local it = m.items[m.sel]
    if it.sub then
      local sm = it.sub()
      sm.parent = m
      sm.sel = 1
      for i, x in ipairs(sm.items) do if x.active then sm.sel = i end end
      state.menu = sm
    elseif it.run then
      it.run()
      if state.menu == m and m.kind ~= "adjust" then
        -- Auswahl übernommen: zurück zum übergeordneten Menü (mit aktualisierten Werten)
        if m.parent then
          local p = main_menu(); p.sel = m.parent.sel; state.menu = p
        else
          close_menu(); return
        end
      end
    end
  end
  render()
end

-- ------------------------------------------------------------------ Tasten
local function seek_step(held)
  if held > 4 then return 120 elseif held > 2.5 then return 60 elseif held > 1.0 then return 30 end
  return 10
end

local function seek(dir, held)
  if is_image() then
    mp.commandv("playlist-" .. (dir > 0 and "next" or "prev"))
    return
  end
  local step = seek_step(held or 0)
  mp.commandv("seek", tostring(dir * step), "relative+exact")
  show_osd(3)
end

local function key_handler(name, fn)
  return function(e)
    -- e.event: down / repeat / up / press
    fn(e)
  end
end

local function on_dir(dir_name)
  return function(e)
    if e.event == "up" then state.seek_held = nil; return end
    if state.menu then
      if e.event == "down" or e.event == "press" or e.event == "repeat" then menu_key(dir_name) end
      return
    end
    if dir_name == "up" then
      if e.event ~= "repeat" then open_menu(main_menu()) end
      return
    end
    if dir_name == "down" then
      if e.event ~= "repeat" then state.info = not state.info; show_osd(8) end
      return
    end
    local dir = dir_name == "right" and 1 or -1
    if e.event == "down" or e.event == "press" then
      state.seek_held = {since = now()}
      seek(dir, 0)
    elseif e.event == "repeat" and state.seek_held then
      local held = now() - state.seek_held.since
      if is_image() then return end          -- Bilder nicht im Dauerlauf durchblättern
      if (state.seek_held.last or 0) + 0.25 < now() then
        state.seek_held.last = now()
        seek(dir, held)
      end
    end
  end
end

local function on_ok(e)
  if e.event == "up" or e.event == "repeat" then return end
  if state.menu then menu_key("ok"); return end
  if state.info then state.info = false end
  local paused = not mp.get_property_bool("pause")
  mp.set_property_bool("pause", paused)
  flash(paused and "❚❚" or "▶")
  show_osd(paused and 4 or 3)
end

local function on_back(e)
  if e.event == "up" or e.event == "repeat" then return end
  if state.menu then menu_key("back"); return end
  if state.info then state.info = false; render(); return end
  mp.command("quit-watch-later")
end

local function bind(keys, name, fn)
  for i, k in ipairs(keys) do
    mp.add_forced_key_binding(k, name .. i, fn, {complex = true, repeatable = true})
  end
end

bind({"UP", "KP8"}, "wtv-up", on_dir("up"))
bind({"DOWN", "KP2"}, "wtv-down", on_dir("down"))
bind({"LEFT", "KP4"}, "wtv-left", on_dir("left"))
bind({"RIGHT", "KP6"}, "wtv-right", on_dir("right"))
bind({"ENTER", "KP_ENTER", "SPACE", "PLAYPAUSE", "MBTN_LEFT"}, "wtv-ok", on_ok)
bind({"ESC", "BS", "GO_BACK", "MBTN_RIGHT", "STOP"}, "wtv-back", on_back)
bind({"PLAY"}, "wtv-play", function(e) if e.event == "down" or e.event == "press" then mp.set_property_bool("pause", false); show_osd(3) end end)
bind({"PAUSE"}, "wtv-pause", function(e) if e.event == "down" or e.event == "press" then mp.set_property_bool("pause", true); show_osd(4) end end)
bind({"NEXT", "CHANNEL_UP", "PGUP", ">"}, "wtv-next", function(e) if e.event == "down" or e.event == "press" then mp.command("playlist-next"); show_osd(4) end end)
bind({"PREV", "CHANNEL_DOWN", "PGDWN", "<"}, "wtv-prev", function(e) if e.event == "down" or e.event == "press" then mp.command("playlist-prev"); show_osd(4) end end)
bind({"FORWARD"}, "wtv-ff", function(e) if e.event ~= "up" then seek(1, 3) end end)
bind({"REWIND"}, "wtv-rew", function(e) if e.event ~= "up" then seek(-1, 3) end end)
bind({"F1"}, "wtv-red", function(e) if e.event == "down" or e.event == "press" then local m = track_menu("audio"); open_menu(m) end end)
bind({"F2"}, "wtv-green", function(e) if e.event == "down" or e.event == "press" then local m = track_menu("sub"); open_menu(m) end end)
bind({"F3"}, "wtv-yellow", function(e) if e.event == "down" or e.event == "press" then
  open_menu(choice_menu(L.speed, "speed", {{"0.5", "0,5x"}, {"0.75", "0,75x"}, {"1", "1x"}, {"1.25", "1,25x"}, {"1.5", "1,5x"}, {"2", "2x"}})) end end)
bind({"F4", "i"}, "wtv-blue", function(e) if e.event == "down" or e.event == "press" then state.info = not state.info; show_osd(8) end end)
for d = 0, 9 do
  bind({tostring(d), "KP" .. d}, "wtv-num" .. d, function(e)
    if e.event == "down" or e.event == "press" then
      local dur = mp.get_property_number("duration")
      if dur and not is_image() then mp.commandv("seek", tostring(d * 10), "absolute-percent+exact"); show_osd(3) end
    end
  end)
end

-- ------------------------------------------------------------------ Fortschritt für "Weiterschauen" in WangertTV
local function read_progress()
  if PROGRESS_FILE == "" then return {} end
  local fh = io.open(PROGRESS_FILE, "r")
  if not fh then return {} end
  local ok, data = pcall(utils.parse_json, fh:read("*a"))
  fh:close()
  return (ok and type(data) == "table") and data or {}
end

local current = {}   -- Datei, die gerade läuft (bei "end-file" sind die Eigenschaften teils schon weg)

local function save_progress(eof)
  if PROGRESS_FILE == "" then return end
  local path = mp.get_property("path") or current.path
  if not path or current.image then return end
  local pos = mp.get_property_number("time-pos") or current.pos
  local dur = mp.get_property_number("duration") or current.duration
  if not dur or dur < 60 then return end
  if eof then pos = dur end
  if not pos then return end
  current.pos = pos
  local entry = {path = path, title = mp.get_property("media-title") or current.title, pos = pos, duration = dur,
                 audio = current.audio or nil, time = os.time(), done = (dur - pos) < math.max(30, dur * 0.03)}
  local list = read_progress()
  local out = {entry}
  for _, e in ipairs(list) do
    if e.path ~= path and #out < 40 then out[#out + 1] = e end
  end
  local tmp = PROGRESS_FILE .. ".tmp"
  fh = io.open(tmp, "w")
  if fh then
    fh:write(utils.format_json(out))
    fh:close()
    os.rename(tmp, PROGRESS_FILE)
  end
end

mp.add_periodic_timer(15, function() save_progress(false) end)
mp.register_event("end-file", function(e)
  save_progress(e and e.reason == "eof")
  current = {}
end)
mp.register_event("shutdown", function() save_progress(false) end)

-- ------------------------------------------------------------------ Ablauf
mp.register_event("file-loaded", function()
  current = {path = mp.get_property("path"), title = mp.get_property("media-title"),
             duration = mp.get_property_number("duration"), image = is_image(), audio = is_audio_only()}
  state.menu = nil
  state.info = false
  local start = mp.get_property_number("time-pos") or 0
  if start < 5 and not current.image and not skip_resume then
    -- Weiterschauen wie bei Netflix: auch ohne mpv-Merkdatei (z. B. nach Stromausfall) an der letzten Stelle weiter –
    -- in einer Wiedergabeliste für jede Datei einzeln
    for _, e in ipairs(read_progress()) do
      if e.path == current.path then
        if not e.done and tonumber(e.pos) and e.pos > 10 then
          mp.commandv("seek", tostring(e.pos), "absolute+exact")
          start = e.pos
        end
        break
      end
    end
  end
  skip_resume = false
  if start > 5 then flash(L.resumed .. " " .. fmt_time(start), 3) end
  show_osd(5)
end)
mp.observe_property("pause", "bool", function() render() end)
mp.add_periodic_timer(0.5, render)
msg.info("WangertTV-Oberfläche geladen")
