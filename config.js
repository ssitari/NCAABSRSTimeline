// ============================================================
//  config.js — the only file to edit when adapting this tool
// ============================================================

export const SRS_FILE = './srs_ratings.csv';
export const COLORS_FILE = './team_colors.csv';
export const CHAMPIONSHIPS_FILE = './championships.csv';
export const CURATED_FILE = './curated_teams.csv';

// Championship markers (only drawn on hovered/pinned lines)
export const CHAMPIONSHIP_MARKER_RADIUS = 4.5;
export const CHAMPIONSHIP_MARKER_FILL = '#ffd54a';
export const CHAMPIONSHIP_MARKER_STROKE = '#7a5c00';

// Full available year range -- the outer bound of the context brush.
// (SR's SRS data starts with the 1949-50 season, labelled 1950.)
export const FULL_YEAR_RANGE = [1950, 2026];

// The chart opens on the modern era (64-team field / shot clock / 3-pt line);
// the context brush still lets the viewer zoom back to 1950.
export const DEFAULT_YEAR_RANGE = [1985, FULL_YEAR_RANGE[1]];

export const MOVING_AVERAGE_WINDOW = 5; // seasons, trailing

// Background field: non-curated programs (never a power-conf member, never
// AP/SRS top 25, never a champion). Very faint -- present, hover-able, but
// not competing for attention.
export const BASE_COLOR = '#c9c9c9';
export const BASE_OPACITY = 0.16;
export const BASE_HOVER_OPACITY = 0.9;

// Curated programs plot in their own (real or conference-derived) color at
// low opacity by default, so the opening view already reads as "the teams
// that have mattered" without any interaction.
export const CURATED_OPACITY = 0.5;
export const CURATED_STROKE_WIDTH = 1;

// Pinned/selected entities
export const PINNED_STROKE_WIDTH = 2.25;
export const HOVER_STROKE_WIDTH = 2;
export const BASE_STROKE_WIDTH = 1;

export const TOP_N_TEAMS = 10;

// Conference fallback palette (evenly spaced hues), used for a conference's
// average line and for member teams with no real sourced color.
export const CONFERENCE_HUE_SATURATION = 55;
export const CONFERENCE_HUE_LIGHTNESS = 45;
