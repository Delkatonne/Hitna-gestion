/* Dépenses — achats de produits, renouvellement de stock, transport… (React, données : /api/depenses) */
(function () {
  'use strict';
  var HX = window.HX, html = HX.html, useState = HX.useState;
  var Kpi = HX.Kpi, Carte = HX.Carte, Icone = HX.Icone, Vide = HX.Vide, Graphique = HX.Graphique;

  function formVide(categories, modes) {
    return {
      date: HX.isoAujourdhui(), categorie: categories[0] || 'Autre', motif: '', montant: '',
      fournisseur: '', mode_paiement: modes[0] || 'Espèces', compte_id: '', reference: '', notes: ''
    };
  }

  function FormulaireDepense(p) {
    var etat = useState(formVide(p.categories, p.modes));
    var f = etat[0];
    var envoi = useState(false);
    var erreur = useState('');

    function maj(champ) {
      return function (e) {
        var v = e.target.value;
        etat[1](function (s) { var n = Object.assign({}, s); n[champ] = v; return n; });
      };
    }
    function soumettre(e) {
      e.preventDefault();
      erreur[1]('');
      if (!f.motif.trim()) { erreur[1]('Indiquez le motif de la dépense.'); return; }
      if (!(parseFloat(f.montant) > 0)) { erreur[1]('Le montant doit être supérieur à 0.'); return; }
      envoi[1](true);
      HX.api('/api/depenses', {
        method: 'POST',
        body: {
          date: f.date, categorie: f.categorie, motif: f.motif, montant: parseFloat(f.montant),
          fournisseur: f.fournisseur, mode_paiement: f.mode_paiement,
          compte_id: f.compte_id ? parseInt(f.compte_id, 10) : null, reference: f.reference, notes: f.notes
        }
      }).then(
        function (r) { p.onSucces(r); },
        function (err) { erreur[1](err.message); envoi[1](false); }
      );
    }

    var comptes = p.comptes;
    return html`<${HX.Modal} titre="Nouvelle dépense" onClose=${p.onClose}
      pied=${html`<button type="button" className="btn btn-outline-secondary" onClick=${p.onClose}>Annuler</button>
                  <button type="submit" form="form-depense" className="btn btn-primary" disabled=${envoi[0]}>
                    ${envoi[0] ? 'Enregistrement…' : 'Enregistrer la dépense'}
                  </button>`}>
      <form id="form-depense" onSubmit=${soumettre}>
        ${erreur[0] ? html`<div className="alert alert-danger py-2">${erreur[0]}</div>` : null}
        <div className="row g-3">
          <div className="col-sm-6">
            <label className="form-label">Date</label>
            <${HX.DateField} value=${f.date} onChange=${function (iso) { etat[1](function (s) { return Object.assign({}, s, { date: iso }); }); }} />
          </div>
          <div className="col-sm-6">
            <label className="form-label">Catégorie</label>
            <select className="form-select" value=${f.categorie} onChange=${maj('categorie')}>
              ${p.categories.map(function (c) { return html`<option key=${c} value=${c}>${c}</option>`; })}
            </select>
          </div>
          <div className="col-12">
            <label className="form-label">Motif de la dépense <span className="text-danger">*</span></label>
            <input type="text" className="form-control" value=${f.motif} onChange=${maj('motif')} maxLength="200"
                   placeholder="Ex. : achat de 20 cartons de riz, renouvellement du stock de boissons…" required />
          </div>
          <div className="col-sm-6">
            <label className="form-label">Montant (FCFA) <span className="text-danger">*</span></label>
            <input type="number" inputMode="decimal" min="0" step="any" className="form-control" value=${f.montant}
                   onChange=${maj('montant')} required />
          </div>
          <div className="col-sm-6">
            <label className="form-label">Mode de paiement</label>
            <select className="form-select" value=${f.mode_paiement} onChange=${maj('mode_paiement')}>
              ${p.modes.map(function (m) { return html`<option key=${m} value=${m}>${m}</option>`; })}
            </select>
          </div>
          <div className="col-sm-6">
            <label className="form-label">Fournisseur / bénéficiaire</label>
            <input type="text" className="form-control" value=${f.fournisseur} onChange=${maj('fournisseur')} maxLength="120" />
          </div>
          <div className="col-sm-6">
            <label className="form-label">N° de facture / référence</label>
            <input type="text" className="form-control" value=${f.reference} onChange=${maj('reference')} maxLength="80" />
          </div>
          <div className="col-12">
            <label className="form-label">Compte débité (trésorerie)</label>
            <select className="form-select" value=${f.compte_id} onChange=${maj('compte_id')}>
              <option value="">Ne pas toucher à la trésorerie</option>
              ${comptes.map(function (c) {
                return html`<option key=${c.id} value=${c.id}>${c.nom} — solde ${HX.fmtMoney(c.solde)}</option>`;
              })}
            </select>
            ${comptes.length === 0 ? html`<div className="form-text">Aucun compte de trésorerie : créez-en un depuis la page Trésorerie pour que les dépenses sortent automatiquement d'une caisse ou d'un compte.</div>` : null}
          </div>
          <div className="col-12">
            <label className="form-label">Notes</label>
            <textarea className="form-control" rows="2" value=${f.notes} onChange=${maj('notes')} maxLength="500"></textarea>
          </div>
        </div>
      </form>
    <//>`;
  }

  function Depenses() {
    var debutEt = useState(HX.isoDebutMois(0));
    var finEt = useState(HX.isoAujourdhui());
    var catEt = useState('');
    var rechEt = useState('');
    var appliques = useState({ debut: HX.isoDebutMois(0), fin: HX.isoAujourdhui(), categorie: '', q: '' });
    var modalEt = useState(false);
    var notice = HX.useNotice();

    var a = appliques[0];
    var url = '/api/depenses?debut=' + a.debut + '&fin=' + a.fin +
      (a.categorie ? '&categorie=' + encodeURIComponent(a.categorie) : '') +
      (a.q ? '&q=' + encodeURIComponent(a.q) : '');
    var f = HX.useFetch(url);

    function appliquer(e) {
      if (e) e.preventDefault();
      appliques[1]({ debut: debutEt[0], fin: finEt[0], categorie: catEt[0], q: rechEt[0].trim() });
    }
    function periode(debut, fin) {
      debutEt[1](debut); finEt[1](fin);
      appliques[1]({ debut: debut, fin: fin, categorie: catEt[0], q: rechEt[0].trim() });
    }
    function supprimer(dep) {
      var msg = 'Supprimer la dépense « ' + dep.motif + ' » (' + HX.fmtMoney(dep.montant) + ') ?' +
        (dep.compte_id ? '\nLe montant sera aussi retiré des mouvements du compte « ' + dep.compte + ' ».' : '');
      if (!window.confirm(msg)) return;
      HX.api('/api/depenses/' + dep.id, { method: 'DELETE' }).then(
        function () { notice[1]({ type: 'succes', texte: 'Dépense supprimée.' }); f.recharger(); },
        function (err) { notice[1]({ type: 'erreur', texte: err.message }); }
      );
    }
    function apresCreation(r) {
      modalEt[1](false);
      notice[1]({ type: r.avertissement ? 'attention' : 'succes',
                  texte: 'Dépense enregistrée.' + (r.avertissement ? ' ' + r.avertissement : '') });
      f.recharger();
    }

    if (f.chargement && !f.data) return html`<${HX.Chargement} />`;
    if (f.erreur && !f.data) return html`<${HX.ErreurBox} message=${f.erreur} onRetry=${f.recharger} />`;

    var d = f.data;
    var principale = d.par_categorie.length ? d.par_categorie[0] : null;
    var graphCat = {
      labels: d.par_categorie.map(function (c) { return c.categorie; }),
      datasets: [{ data: d.par_categorie.map(function (c) { return c.total; }), backgroundColor: HX.PALETTE, borderWidth: 2 }]
    };
    var graphMois = {
      labels: d.par_mois.map(function (m) { return m.libelle; }),
      datasets: [{ label: 'Dépenses', data: d.par_mois.map(function (m) { return m.total; }), backgroundColor: 'rgba(245,159,0,.9)', borderRadius: 6 }]
    };

    return html`<div className="hx-page">
      <${HX.Notice} notice=${notice[0]} onClose=${function () { notice[1](null); }} />

      ${!d.boutique_active ? html`<div className="alert alert-info mb-0">
        <${Icone} nom="info-circle" /> Pour enregistrer une dépense, choisissez d'abord une boutique active (sélecteur en haut de page).
      </div>` : null}

      <div className="hx-card">
        <form onSubmit=${appliquer} className="row g-2 align-items-end">
          <div className="col-6 col-md-auto">
            <label className="form-label mb-1">Du</label>
            <${HX.DateField} value=${debutEt[0]} onChange=${debutEt[1]} label="Date de début" />
          </div>
          <div className="col-6 col-md-auto">
            <label className="form-label mb-1">Au</label>
            <${HX.DateField} value=${finEt[0]} onChange=${finEt[1]} label="Date de fin" />
          </div>
          <div className="col-md-3">
            <label className="form-label mb-1">Catégorie</label>
            <select className="form-select" value=${catEt[0]} onChange=${function (e) { catEt[1](e.target.value); }}>
              <option value="">Toutes</option>
              ${d.categories.map(function (c) { return html`<option key=${c} value=${c}>${c}</option>`; })}
            </select>
          </div>
          <div className="col-md">
            <label className="form-label mb-1">Recherche</label>
            <input type="search" className="form-control" placeholder="Motif, fournisseur, référence…"
                   value=${rechEt[0]} onChange=${function (e) { rechEt[1](e.target.value); }} />
          </div>
          <div className="col-12 col-md-auto d-flex gap-2">
            <button type="submit" className="btn btn-primary"><${Icone} nom="funnel" /> Filtrer</button>
            <button type="button" className="btn btn-success" disabled=${!d.boutique_active}
                    onClick=${function () { modalEt[1](true); }}><${Icone} nom="plus-lg" /> Nouvelle dépense</button>
          </div>
        </form>
        <div className="d-flex flex-wrap gap-2 mt-3">
          <button type="button" className="btn btn-sm btn-outline-secondary" onClick=${function () { periode(HX.isoDebutMois(0), HX.isoAujourdhui()); }}>Ce mois</button>
          <button type="button" className="btn btn-sm btn-outline-secondary" onClick=${function () { periode(HX.isoDebutMois(-1), HX.isoFinMois(-1)); }}>Mois dernier</button>
          <button type="button" className="btn btn-sm btn-outline-secondary" onClick=${function () { periode(HX.isoIlYaJours(29), HX.isoAujourdhui()); }}>30 derniers jours</button>
          <button type="button" className="btn btn-sm btn-outline-secondary" onClick=${function () { periode(HX.isoIlYaJours(364), HX.isoAujourdhui()); }}>12 mois</button>
        </div>
      </div>

      <div className="row g-3">
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Total des dépenses" icone="receipt" couleur="orange" valeur=${HX.fmtNum(d.total)} unite="FCFA"
            sous=${'Du ' + HX.fmtDate(d.debut) + ' au ' + HX.fmtDate(d.fin)} />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Nombre de dépenses" icone="list-ol" valeur=${HX.fmtNum(d.nb)} sous=${d.nb >= 500 ? '500 dernières affichées' : 'Sur la période'} />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Dépense moyenne" icone="calculator" couleur="gris" valeur=${HX.fmtNum(d.nb ? d.total / d.nb : 0)} unite="FCFA" sous="Par dépense" />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Poste principal" icone="pie-chart" valeur=${principale ? principale.categorie : '-'}
            sous=${principale ? HX.fmtMoney(principale.total) : 'Aucune dépense'} />
        </div>
      </div>

      <div className="row g-3">
        <div className="col-lg-5">
          <${Carte} titre="Répartition par catégorie" icone="pie-chart">
            ${d.par_categorie.length
              ? html`<${Graphique} type="doughnut" data=${graphCat} legende=${true} taille="petit" options=${{ plugins: { tooltip: HX.INFO_BULLE_ARGENT } }} />`
              : html`<${Vide} icone="receipt" texte="Aucune dépense sur cette période" />`}
          <//>
        </div>
        <div className="col-lg-7">
          <${Carte} titre="Dépenses des 6 derniers mois" icone="bar-chart-line">
            <${Graphique} type="bar" data=${graphMois} taille="petit"
              options=${{ scales: { y: HX.AXE_ARGENT, x: { grid: { display: false } } }, plugins: { tooltip: HX.INFO_BULLE_ARGENT } }} />
          <//>
        </div>
      </div>

      <${Carte} titre="Liste des dépenses" icone="journal-text">
        ${d.items.length ? html`<div className="hx-table-wrap"><table className="hx-table">
          <thead><tr>
            <th>Date</th><th>Catégorie</th><th>Motif</th><th>Fournisseur</th><th>Paiement</th><th>Compte</th>
            <th className="num">Montant</th><th></th>
          </tr></thead>
          <tbody>
          ${d.items.map(function (x) {
            return html`<tr key=${x.id}>
              <td>${HX.fmtDate(x.date)}</td>
              <td><span className="hx-badge">${x.categorie}</span></td>
              <td>${x.motif}
                ${x.reference ? html`<div className="text-muted small">Réf. ${x.reference}</div>` : null}
                ${x.notes ? html`<div className="text-muted small">${x.notes}</div>` : null}
              </td>
              <td>${x.fournisseur || '-'}</td>
              <td>${x.mode_paiement || '-'}</td>
              <td>${x.compte || '-'}</td>
              <td className="num"><strong>${HX.fmtMoney(x.montant)}</strong></td>
              <td className="num">
                <button type="button" className="btn btn-sm btn-outline-danger" title="Supprimer" aria-label="Supprimer la dépense"
                        onClick=${function () { supprimer(x); }}><${Icone} nom="trash3" /></button>
              </td>
            </tr>`;
          })}
          </tbody>
          <tfoot><tr><td colSpan="6">Total</td><td className="num">${HX.fmtMoney(d.total)}</td><td></td></tr></tfoot>
        </table></div>` : html`<${Vide} icone="receipt" texte="Aucune dépense sur cette période" />`}
      <//>

      ${modalEt[0] ? html`<${FormulaireDepense} categories=${d.categories} modes=${d.modes_paiement} comptes=${d.comptes}
          onClose=${function () { modalEt[1](false); }} onSucces=${apresCreation} />` : null}
    </div>`;
  }

  HX.monter(Depenses);
})();
