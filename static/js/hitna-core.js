/* HITNA — noyau React partagé par les pages dashboard, statistiques, dépenses, trésorerie.
   Pas d'étape de build : React 18 + htm (JSX en littéraux de gabarit) chargés depuis un CDN.
   Chaque page appelle HX.monter(Composant) une fois le DOM prêt. */
(function () {
  'use strict';

  var React = window.React;
  var ReactDOM = window.ReactDOM;
  var useState = React.useState, useEffect = React.useEffect, useRef = React.useRef;
  var html = window.htm.bind(React.createElement);

  // ─────────────── Formats (toutes les dates : jj/mm/aaaa) ───────────────
  var RE_ISO = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/;

  function fmtDate(v, avecHeure) {
    if (!v) return '';
    var m = RE_ISO.exec(String(v));
    if (!m) return String(v);
    var s = m[3] + '/' + m[2] + '/' + m[1];
    if (avecHeure && m[4]) s += ' ' + m[4] + ':' + m[5];
    return s;
  }
  function fmtDateHeure(v) { return fmtDate(v, true); }
  function fmtJourMois(v) { var m = RE_ISO.exec(String(v || '')); return m ? m[3] + '/' + m[2] : String(v || ''); }
  function fmtNum(n) { return Math.round(Number(n) || 0).toLocaleString('fr-FR'); }
  function fmtMoney(n) { return fmtNum(n) + ' FCFA'; }
  function fmtQte(n) { return (Math.round((Number(n) || 0) * 100) / 100).toLocaleString('fr-FR'); }
  function fmtCompact(n) {
    var x = Number(n) || 0, a = Math.abs(x);
    if (a >= 1e6) return (Math.round(x / 1e5) / 10).toLocaleString('fr-FR') + ' M';
    if (a >= 1e3) return Math.round(x / 1e3).toLocaleString('fr-FR') + ' k';
    return String(Math.round(x));
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function isoDe(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function isoAujourdhui() { return isoDe(new Date()); }
  function isoDebutMois(decalage) {
    var d = new Date(); d.setDate(1); d.setMonth(d.getMonth() + (decalage || 0)); return isoDe(d);
  }
  function isoFinMois(decalage) {
    var d = new Date(); d.setDate(1); d.setMonth(d.getMonth() + (decalage || 0) + 1); d.setDate(0); return isoDe(d);
  }
  function isoIlYaJours(n) { var d = new Date(); d.setDate(d.getDate() - n); return isoDe(d); }

  function isoVersFr(iso) { return fmtDate(iso); }
  function frVersIso(txt) {
    var m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(txt || '');
    if (!m) return null;
    var j = +m[1], mo = +m[2], a = +m[3];
    var d = new Date(a, mo - 1, j);
    if (d.getFullYear() !== a || d.getMonth() !== mo - 1 || d.getDate() !== j) return null;
    return a + '-' + m[2] + '-' + m[1];
  }

  // ─────────────── Appels API ───────────────
  function api(url, options) {
    var opts = Object.assign({ credentials: 'same-origin', headers: { Accept: 'application/json' } }, options || {});
    if (opts.body && typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
      opts.headers['Content-Type'] = 'application/json';
    }
    return fetch(url, opts).then(
      function (res) {
        return res.json().catch(function () { return null; }).then(function (data) {
          if (res.status === 401) { window.location.href = '/login'; throw new Error('Session expirée'); }
          if (!res.ok) throw new Error((data && data.erreur) || 'Erreur ' + res.status);
          return data;
        });
      },
      function () { throw new Error('Connexion impossible. Vérifiez votre réseau.'); }
    );
  }

  function useFetch(url) {
    var etat = useState({ data: null, erreur: null, chargement: true });
    var tick = useState(0);
    useEffect(function () {
      var annule = false;
      etat[1](function (s) { return { data: s.data, erreur: null, chargement: true }; });
      api(url).then(
        function (d) { if (!annule) etat[1]({ data: d, erreur: null, chargement: false }); },
        function (e) { if (!annule) etat[1]({ data: null, erreur: e.message, chargement: false }); }
      );
      return function () { annule = true; };
    }, [url, tick[0]]);
    return {
      data: etat[0].data, erreur: etat[0].erreur, chargement: etat[0].chargement,
      recharger: function () { tick[1](function (t) { return t + 1; }); }
    };
  }

  // ─────────────── Composants ───────────────
  var PALETTE = ['#2a5298', '#198754', '#f59f00', '#dc3545', '#6f42c1', '#0dcaf0', '#fd7e14', '#20c997'];

  function Icone(p) { return html`<i className=${'bi bi-' + p.nom + (p.classe ? ' ' + p.classe : '')}></i>`; }

  function Variation(p) {
    if (p.valeur === null || p.valeur === undefined) return null;
    var haut = p.valeur >= 0;
    return html`<span className=${haut ? 'hx-var-haut' : 'hx-var-bas'}>
      <${Icone} nom=${haut ? 'arrow-up-right' : 'arrow-down-right'} /> ${Math.abs(p.valeur).toLocaleString('fr-FR')} %
    </span>`;
  }

  function Kpi(p) {
    return html`<div className=${'hx-kpi ' + (p.couleur || '')}>
      <div className="hx-kpi-lib"><${Icone} nom=${p.icone || 'graph-up'} /> ${p.titre}</div>
      <div className="hx-kpi-val">${p.valeur}${p.unite ? html` <small>${p.unite}</small>` : null}</div>
      ${p.sous ? html`<div className="hx-kpi-sous">${p.sous}</div>` : null}
    </div>`;
  }

  function Carte(p) {
    return html`<div className="hx-card">
      <div className="hx-card-tete">
        <h3 className="hx-card-titre">${p.icone ? html`<${Icone} nom=${p.icone} />` : null} ${p.titre}</h3>
        ${p.actions || null}
      </div>
      ${p.children}
    </div>`;
  }

  function Vide(p) {
    return html`<div className="hx-vide"><${Icone} nom=${p.icone || 'inbox'} />${p.texte || 'Aucune donnée'}</div>`;
  }

  function Chargement() {
    return html`<div className="hx-chargement">
      <div className="spinner-border text-primary" role="status"></div>
      <div className="mt-2">Chargement…</div>
    </div>`;
  }

  function ErreurBox(p) {
    return html`<div className="alert alert-danger d-flex align-items-center justify-content-between gap-2" role="alert">
      <span><${Icone} nom="exclamation-triangle" /> ${p.message}</span>
      ${p.onRetry ? html`<button className="btn btn-sm btn-outline-danger" onClick=${p.onRetry}>Réessayer</button>` : null}
    </div>`;
  }

  function Notice(p) {
    if (!p.notice) return null;
    var n = p.notice;
    var cls = n.type === 'erreur' ? 'danger' : (n.type === 'attention' ? 'warning' : 'success');
    var ico = n.type === 'erreur' ? 'x-octagon' : (n.type === 'attention' ? 'exclamation-triangle' : 'check-circle');
    return html`<div className=${'alert alert-' + cls + ' alert-dismissible hx-notice'} role="alert">
      <${Icone} nom=${ico} /> ${n.texte}
      <button type="button" className="btn-close" aria-label="Fermer" onClick=${p.onClose}></button>
    </div>`;
  }

  // Message temporaire (disparaît seul après 7 s)
  function useNotice() {
    var etat = useState(null);
    useEffect(function () {
      if (!etat[0]) return undefined;
      var t = setTimeout(function () { etat[1](null); }, 7000);
      return function () { clearTimeout(t); };
    }, [etat[0]]);
    return [etat[0], etat[1]];
  }

  function Modal(p) {
    useEffect(function () {
      function touche(e) { if (e.key === 'Escape') p.onClose(); }
      document.addEventListener('keydown', touche);
      return function () { document.removeEventListener('keydown', touche); };
    }, []);
    return html`<div className="hx-modal-fond" onMouseDown=${function (e) { if (e.target === e.currentTarget) p.onClose(); }}>
      <div className="hx-modal" role="dialog" aria-modal="true" aria-label=${p.titre}>
        <div className="hx-modal-tete">
          <h5>${p.titre}</h5>
          <button type="button" className="btn-close" aria-label="Fermer" onClick=${p.onClose}></button>
        </div>
        <div className="hx-modal-corps">${p.children}</div>
        ${p.pied ? html`<div className="hx-modal-pied">${p.pied}</div>` : null}
      </div>
    </div>`;
  }

  // Champ date : saisie et affichage toujours en jj/mm/aaaa, quel que soit le navigateur.
  // value / onChange manipulent la forme ISO (aaaa-mm-jj) attendue par le serveur.
  function DateField(p) {
    var txt = useState(isoVersFr(p.value));
    var cache = useRef(null);
    useEffect(function () { txt[1](isoVersFr(p.value)); }, [p.value]);

    function saisie(e) {
      var d = e.target.value.replace(/\D/g, '').slice(0, 8);
      var sortie = d;
      if (d.length > 4) sortie = d.slice(0, 2) + '/' + d.slice(2, 4) + '/' + d.slice(4);
      else if (d.length > 2) sortie = d.slice(0, 2) + '/' + d.slice(2);
      txt[1](sortie);
      var iso = frVersIso(sortie);
      if (iso) p.onChange(iso);
    }
    function ouvrir() {
      var el = cache.current;
      if (!el) return;
      el.value = p.value || '';
      if (el.showPicker) { try { el.showPicker(); } catch (e) { el.focus(); } } else { el.focus(); el.click(); }
    }
    var invalide = txt[0].length === 10 && !frVersIso(txt[0]);
    return html`<div className="hx-date">
      <input type="text" inputMode="numeric" placeholder="jj/mm/aaaa" maxLength="10" autoComplete="off"
             className=${'form-control' + (invalide ? ' is-invalid' : '')} value=${txt[0]}
             onChange=${saisie} aria-label=${p.label || 'Date'} />
      <button type="button" tabIndex="-1" onClick=${ouvrir} aria-label="Ouvrir le calendrier"><${Icone} nom="calendar3" /></button>
      <input type="date" ref=${cache} tabIndex="-1" aria-hidden="true"
             onChange=${function (e) { if (e.target.value) p.onChange(e.target.value); }} />
    </div>`;
  }

  // Graphique Chart.js (chargé par base.html). Recréé quand les données changent.
  function Graphique(p) {
    var canvas = useRef(null);
    var cle = JSON.stringify([p.type, p.data, p.options || null]);
    useEffect(function () {
      if (!canvas.current || !window.Chart) return undefined;
      var base = { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: !!p.legende, position: 'bottom' } } };
      var options = Object.assign({}, base, p.options || {});
      if (p.options && p.options.plugins) options.plugins = Object.assign({}, base.plugins, p.options.plugins);
      var graphique = new window.Chart(canvas.current.getContext('2d'), { type: p.type, data: p.data, options: options });
      return function () { graphique.destroy(); };
    }, [cle]);
    if (!window.Chart) return html`<${Vide} icone="bar-chart" texte="Graphique indisponible (bibliothèque non chargée)" />`;
    return html`<div className=${'hx-chart ' + (p.taille || '')}><canvas ref=${canvas}></canvas></div>`;
  }

  var AXE_ARGENT = {
    beginAtZero: true,
    ticks: { callback: function (v) { return fmtCompact(v); } },
    grid: { color: '#eef1f7' }
  };
  var INFO_BULLE_ARGENT = {
    callbacks: {
      label: function (c) {
        var v = c.parsed && c.parsed.y !== undefined && c.parsed.y !== null ? c.parsed.y : c.parsed;
        if (c.chart.options.indexAxis === 'y') v = c.parsed.x;
        return (c.dataset.label ? c.dataset.label + ' : ' : (c.label ? c.label + ' : ' : '')) + fmtMoney(v);
      }
    }
  };

  function monter(Composant) {
    function lancer() {
      var racine = document.getElementById('app-root');
      if (racine) ReactDOM.createRoot(racine).render(html`<${Composant} />`);
    }
    // Chart.js est chargé en "defer" par base.html : on attend la fin du chargement.
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', lancer);
    else lancer();
  }

  window.HX = {
    React: React, html: html, useState: useState, useEffect: useEffect, useRef: useRef,
    fmtDate: fmtDate, fmtDateHeure: fmtDateHeure, fmtJourMois: fmtJourMois, fmtNum: fmtNum, fmtMoney: fmtMoney,
    fmtQte: fmtQte, fmtCompact: fmtCompact, isoAujourdhui: isoAujourdhui, isoDebutMois: isoDebutMois,
    isoFinMois: isoFinMois, isoIlYaJours: isoIlYaJours, isoVersFr: isoVersFr, frVersIso: frVersIso,
    api: api, useFetch: useFetch, useNotice: useNotice,
    Icone: Icone, Variation: Variation, Kpi: Kpi, Carte: Carte, Vide: Vide, Chargement: Chargement,
    ErreurBox: ErreurBox, Notice: Notice, Modal: Modal, DateField: DateField, Graphique: Graphique,
    PALETTE: PALETTE, AXE_ARGENT: AXE_ARGENT, INFO_BULLE_ARGENT: INFO_BULLE_ARGENT, monter: monter
  };
})();
