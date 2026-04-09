/**
 * xTool S1 Jobs Card — custom Lovelace card for job management.
 *
 * Features:
 *   - Save the current laser job with metadata
 *   - List saved jobs with all properties
 *   - Start a job with confirmation dialog
 *   - Delete saved jobs
 */

class XToolS1JobsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._jobs = [];
    this._saving = false;
    this._starting = null; // title of job being confirmed
    this._error = null;
    this._success = null;
  }

  set hass(hass) {
    this._hass = hass;
  }

  setConfig(config) {
    this._config = config;
  }

  connectedCallback() {
    this._loadJobs();
  }

  async _loadJobs() {
    if (!this._hass) return;
    try {
      const result = await this._hass.callService("xtool_s1", "list_jobs", {}, undefined, true, true);
      this._jobs = result?.response?.jobs || [];
    } catch {
      this._jobs = [];
    }
    this._render();
  }

  async _saveJob(title, description, material, thickness) {
    this._error = null;
    this._success = null;
    try {
      await this._hass.callService("xtool_s1", "save_job", {
        title, description, material, thickness_mm: parseFloat(thickness),
      });
      this._success = `"${title}" gespeichert`;
      this._saving = false;
      await this._loadJobs();
    } catch (e) {
      this._error = e.message || String(e);
      this._render();
    }
  }

  async _startJob(title) {
    this._error = null;
    this._success = null;
    try {
      await this._hass.callService("xtool_s1", "start_job", {
        title, confirm: true,
      });
      this._success = `"${title}" gestartet — Knopf am Laser drücken!`;
      this._starting = null;
      this._render();
    } catch (e) {
      this._error = e.message || String(e);
      this._render();
    }
  }

  async _deleteJob(title) {
    this._error = null;
    this._success = null;
    try {
      await this._hass.callService("xtool_s1", "delete_job", { title });
      this._success = `"${title}" gelöscht`;
      await this._loadJobs();
    } catch (e) {
      this._error = e.message || String(e);
      this._render();
    }
  }

  _render() {
    const jobs = this._jobs;
    const shadow = this.shadowRoot;

    shadow.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 16px; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
        .header h2 { margin: 0; font-size: 18px; }
        .btn { cursor: pointer; padding: 8px 16px; border: none; border-radius: 8px; font-size: 14px; }
        .btn-primary { background: var(--primary-color, #03a9f4); color: white; }
        .btn-danger { background: #f44336; color: white; }
        .btn-success { background: #4caf50; color: white; }
        .btn-secondary { background: var(--secondary-background-color, #e0e0e0); color: var(--primary-text-color); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .job-card { background: var(--card-background-color, #fff); border: 1px solid var(--divider-color, #e0e0e0); border-radius: 12px; padding: 12px; margin-bottom: 8px; }
        .job-title { font-weight: bold; font-size: 16px; margin-bottom: 4px; }
        .job-desc { color: var(--secondary-text-color); font-size: 13px; margin-bottom: 8px; }
        .job-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; font-size: 13px; }
        .job-meta dt { color: var(--secondary-text-color); }
        .job-meta dd { margin: 0; font-weight: 500; }
        .job-actions { display: flex; gap: 8px; margin-top: 10px; }
        .form-group { margin-bottom: 12px; }
        .form-group label { display: block; font-size: 13px; color: var(--secondary-text-color); margin-bottom: 4px; }
        .form-group input, .form-group textarea { width: 100%; padding: 8px; border: 1px solid var(--divider-color, #ccc); border-radius: 8px; font-size: 14px; box-sizing: border-box; background: var(--card-background-color); color: var(--primary-text-color); }
        .form-group textarea { min-height: 60px; resize: vertical; }
        .alert { padding: 10px 14px; border-radius: 8px; margin-bottom: 12px; font-size: 13px; }
        .alert-error { background: #ffebee; color: #c62828; border: 1px solid #ef9a9a; }
        .alert-success { background: #e8f5e9; color: #2e7d32; border: 1px solid #a5d6a7; }
        .confirm-box { background: var(--card-background-color); border: 2px solid var(--primary-color, #03a9f4); border-radius: 12px; padding: 16px; margin-bottom: 12px; }
        .confirm-box h3 { margin: 0 0 12px 0; }
        .confirm-meta { font-size: 14px; line-height: 1.8; }
        .confirm-meta strong { display: inline-block; min-width: 120px; }
        .confirm-warning { background: #fff3e0; color: #e65100; border: 1px solid #ffcc80; border-radius: 8px; padding: 8px 12px; margin: 12px 0; font-size: 12px; }
        .empty { text-align: center; color: var(--secondary-text-color); padding: 32px 0; }
        .divider { border-top: 1px solid var(--divider-color, #e0e0e0); margin: 16px 0; }
      </style>
      <ha-card>
        <div class="header">
          <h2>xTool S1 Jobs</h2>
          <button class="btn btn-primary" id="btn-save">Job speichern</button>
        </div>

        ${this._error ? `<div class="alert alert-error">${this._esc(this._error)}</div>` : ""}
        ${this._success ? `<div class="alert alert-success">${this._esc(this._success)}</div>` : ""}

        ${this._saving ? this._renderSaveForm() : ""}
        ${this._starting ? this._renderConfirm() : ""}

        ${jobs.length === 0 ? '<div class="empty">Keine gespeicherten Jobs</div>' :
          jobs.map(j => this._renderJob(j)).join("")}
      </ha-card>
    `;

    // Event listeners
    shadow.getElementById("btn-save")?.addEventListener("click", () => {
      this._saving = !this._saving;
      this._starting = null;
      this._error = null;
      this._success = null;
      this._render();
    });

    shadow.getElementById("save-submit")?.addEventListener("click", () => {
      const g = (id) => shadow.getElementById(id)?.value?.trim();
      const title = g("save-title");
      const desc = g("save-desc");
      const mat = g("save-material");
      const thick = g("save-thickness");
      if (!title || !desc || !mat || !thick) {
        this._error = "Alle Felder sind Pflicht";
        this._render();
        return;
      }
      this._saveJob(title, desc, mat, thick);
    });

    shadow.getElementById("save-cancel")?.addEventListener("click", () => {
      this._saving = false;
      this._render();
    });

    shadow.querySelectorAll("[data-start]").forEach(btn => {
      btn.addEventListener("click", () => {
        this._starting = btn.dataset.start;
        this._error = null;
        this._success = null;
        this._render();
      });
    });

    shadow.querySelectorAll("[data-delete]").forEach(btn => {
      btn.addEventListener("click", () => {
        if (confirm(`"${btn.dataset.delete}" wirklich löschen?`)) {
          this._deleteJob(btn.dataset.delete);
        }
      });
    });

    shadow.getElementById("confirm-yes")?.addEventListener("click", () => {
      this._startJob(this._starting);
    });

    shadow.getElementById("confirm-no")?.addEventListener("click", () => {
      this._starting = null;
      this._render();
    });

    shadow.getElementById("btn-refresh")?.addEventListener("click", () => {
      this._loadJobs();
    });
  }

  _renderSaveForm() {
    return `
      <div class="divider"></div>
      <h3>Aktuellen Job speichern</h3>
      <div class="form-group">
        <label>Titel *</label>
        <input id="save-title" type="text" placeholder="z.B. Handyhalter">
      </div>
      <div class="form-group">
        <label>Beschreibung *</label>
        <textarea id="save-desc" placeholder="z.B. Ausschnitt für iPhone Hülle"></textarea>
      </div>
      <div class="form-group">
        <label>Material *</label>
        <input id="save-material" type="text" placeholder="z.B. Birke Sperrholz">
      </div>
      <div class="form-group">
        <label>Materialstärke (mm) *</label>
        <input id="save-thickness" type="number" step="0.1" min="0.1" placeholder="3.0">
      </div>
      <div style="display:flex;gap:8px;">
        <button class="btn btn-success" id="save-submit">Speichern</button>
        <button class="btn btn-secondary" id="save-cancel">Abbrechen</button>
      </div>
      <div class="divider"></div>
    `;
  }

  _renderConfirm() {
    const job = this._jobs.find(j => j.title === this._starting);
    if (!job) return "";
    return `
      <div class="confirm-box">
        <h3>Job starten: ${this._esc(job.title)}</h3>
        <div class="confirm-meta">
          <strong>Material:</strong> ${this._esc(job.material)}<br>
          <strong>Dicke:</strong> ${job.thickness_mm} mm<br>
          <strong>Leistung:</strong> ${job.power_percent != null ? job.power_percent + " %" : "—"}<br>
          <strong>Geschwindigkeit:</strong> ${job.speed_mm_per_s != null ? job.speed_mm_per_s + " mm/s" : "—"}<br>
          <strong>Laser-Modul:</strong> ${this._esc(job.laser_module || "—")}<br>
          <strong>Modus:</strong> ${this._esc(job.laser_mode || "—")}<br>
        </div>
        <div class="confirm-warning">
          Verwendung auf eigene Gefahr. Bitte Material, Stärke und Laser-Einstellungen
          vor dem Start prüfen. Nach Bestätigung den Knopf am Laser drücken.
        </div>
        <div style="display:flex;gap:8px;margin-top:12px;">
          <button class="btn btn-success" id="confirm-yes">Bestätigen & Starten</button>
          <button class="btn btn-secondary" id="confirm-no">Abbrechen</button>
        </div>
      </div>
    `;
  }

  _renderJob(job) {
    return `
      <div class="job-card">
        <div class="job-title">${this._esc(job.title)}</div>
        <div class="job-desc">${this._esc(job.description)}</div>
        <dl class="job-meta">
          <dt>Material</dt><dd>${this._esc(job.material)}</dd>
          <dt>Dicke</dt><dd>${job.thickness_mm} mm</dd>
          <dt>Leistung</dt><dd>${job.power_percent != null ? job.power_percent + " %" : "—"}</dd>
          <dt>Geschwindigkeit</dt><dd>${job.speed_mm_per_s != null ? job.speed_mm_per_s + " mm/s" : "—"}</dd>
          <dt>Laser</dt><dd>${this._esc(job.laser_module || "—")}</dd>
          <dt>Gespeichert</dt><dd>${new Date(job.saved_at).toLocaleString()}</dd>
        </dl>
        <div class="job-actions">
          <button class="btn btn-primary" data-start="${this._esc(job.title)}">Starten</button>
          <button class="btn btn-danger" data-delete="${this._esc(job.title)}">Löschen</button>
        </div>
      </div>
    `;
  }

  _esc(s) {
    if (s == null) return "";
    const d = document.createElement("div");
    d.textContent = String(s);
    return d.innerHTML;
  }

  getCardSize() {
    return 3 + this._jobs.length * 2;
  }

  static getStubConfig() {
    return {};
  }
}

customElements.define("xtool-s1-jobs-card", XToolS1JobsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "xtool-s1-jobs-card",
  name: "xTool S1 Jobs",
  description: "Manage saved laser jobs — save, start, delete.",
});
