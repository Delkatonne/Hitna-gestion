/* Trésorerie interne — comptes, entrées/sorties, virements (React, données : /api/tresorerie) */
(function () {
  'use strict';
  var HX = window.HX, html = HX.html, useState = HX.useState;
  var Kpi = HX.Kpi, Carte = HX.Carte, Icone = HX.Icone, Vide = HX.Vide, Graphique = HX.Graphique;

  var ICONES_COMPTE = { 'Caisse': 'cash-stack', 'Banque': 'bank', 'Mobile Money': 'phone', 'Autre': 'wallet2' };

  // ─────────── Formulaire : nouveau compte ───────────
  function FormCompte(p) {
    var nom = useState('');
    var type = useState(p.types[0] || 'Caisse');
    var solde = useState('');
    var envoi = useState(false);
    var erreur = useState('');

    function soumettre(e) {
      e.preventDefault();
      erreur[1]('');
      if (!nom[0].trim()) { erreur[1]('Donnez un nom au compte.'); return; }
      envoi[1](true);
      HX.api('/api/tresorerie/comptes', {
        method: 'POST', body: { nom: nom[0], type: type[0], solde_initial: parseFloat(solde[0]) || 0 }
      }).then(
        function () { p.onSucces('Compte créé.'); },
        function (err) { erreur[1](err.message); envoi[1](false); }
      );
    }
    return html`<${HX.Modal} titre="Nouveau compte de trésorerie" onClose=${p.onClose}
      pied=${html`<button type="button" className="btn btn-outline-secondary" onClick=${p.onClose}>Annuler</button>
                  <button type="submit" form="form-compte" className="btn btn-primary" disabled=${envoi[0]}>Créer le compte</button>`}>
      <form id="form-compte" onSubmit=${soumettre}>
        ${erreur[0] ? html`<div className="alert alert-danger py-2">${erreur[0]}</div>` : null}
        <div className="mb-3">
          <label className="form-label">Nom du compte <span className="text-danger">*</span></label>
          <input type="text" className="form-control" value=${nom[0]} maxLength="80" required
                 placeholder="Ex. : Caisse principale, Compte bancaire, MoMo gérant"
                 onChange=${function (e) { nom[1](e.target.value); }} />
        </div>
        <div className="row g-3">
          <div className="col-sm-6">
            <label className="form-label">Type</label>
            <select className="form-select" value=${type[0]} onChange=${function (e) { type[1](e.target.value); }}>
              ${p.types.map(function (t) { return html`<option key=${t} value=${t}>${t}</option>`; })}
            </select>
          </div>
          <div className="col-sm-6">
            <label className="form-label">Solde de départ (FCFA)</label>
            <input type="number" inputMode="decimal" min="0" step="any" className="form-control" value=${solde[0]}
                   onChange=${function (e) { solde[1](e.target.value); }} />
          </div>
        </div>
      </form>
    <//>`;
  }

  // ─────────── Formulaire : entrée, sortie ou virement ───────────
  function FormMouvement(p) {
    var mode = p.mode; // 'entree' | 'sortie' | 'transfert'
    var f = useState({
      date: HX.isoAujourdhui(), compte: p.comptes[0] ? String(p.comptes[0].id) : '',
      dest: p.comptes[1] ? String(p.comptes[1].id) : '', montant: '', libelle: ''
    });
    var envoi = useState(false);
    var erreur = useState('');
    var v = f[0];

    function maj(champ) {
      return function (e) {
        var val = e.target.value;
        f[1](function (s) { var n = Object.assign({}, s); n[champ] = val; return n; });
      };
    }
    function soumettre(e) {
      e.preventDefault();
      erreur[1]('');
      var montant = parseFloat(v.montant);
      if (!(montant > 0)) { erreur[1]('Le montant doit être supérieur à 0.'); return; }
      var requete;
      if (mode === 'transfert') {
        if (!v.compte || !v.dest || v.compte === v.dest) { erreur[1]('Choisissez deux comptes différents.'); return; }
        requete = HX.api('/api/tresorerie/transfert', { method: 'POST', body: {
          compte_source: parseInt(v.compte, 10), compte_dest: parseInt(v.dest, 10), montant: montant, date: v.date, libelle: v.libelle } });
      } else {
        if (!v.libelle.trim()) { erreur[1]('Le libellé est obligatoire.'); return; }
        requete = HX.api('/api/tresorerie/mouvements', { method: 'POST', body: {
          compte_id: parseInt(v.compte, 10), type: mode, montant: montant, date: v.date, libelle: v.libelle } });
      }
      envoi[1](true);
      requete.then(
        function (r) { p.onSucces(r); },
        function (err) { erreur[1](err.message); envoi[1](false); }
      );
    }

    var titre = mode === 'entree' ? 'Entrée d\'argent' : (mode === 'sortie' ? 'Sortie d\'argent' : 'Virement entre comptes');
    var optionsComptes = function () {
      return p.comptes.map(function (c) {
        return html`<option key=${c.id} value=${c.id}>${c.nom} — ${HX.fmtMoney(c.solde)}</option>`;
      });
    };
    return html`<${HX.Modal} titre=${titre} onClose=${p.onClose}
      pied=${html`<button type="button" className="btn btn-outline-secondary" onClick=${p.onClose}>Annuler</button>
                  <button type="submit" form="form-mouvement" className="btn btn-primary" disabled=${envoi[0]}>Enregistrer</button>`}>
      <form id="form-mouvement" onSubmit=${soumettre}>
        ${erreur[0] ? html`<div className="alert alert-danger py-2">${erreur[0]}</div>` : null}
        <div className="row g-3">
          <div className="col-sm-6">
            <label className="form-label">Date</label>
            <${HX.DateField} value=${v.date} onChange=${function (iso) { f[1](function (s) { return Object.assign({}, s, { date: iso }); }); }} />
          </div>
          <div className="col-sm-6">
            <label className="form-label">Montant (FCFA) <span className="text-danger">*</span></label>
            <input type="number" inputMode="decimal" min="0" step="any" className="form-control" value=${v.montant} onChange=${maj('montant')} required />
          </div>
          <div className=${mode === 'transfert' ? 'col-sm-6' : 'col-12'}>
            <label className="form-label">${mode === 'transfert' ? 'Compte débité' : 'Compte'}</label>
            <select className="form-select" value=${v.compte} onChange=${maj('compte')}>${optionsComptes()}</select>
          </div>
          ${mode === 'transfert' ? html`<div className="col-sm-6">
            <label className="form-label">Compte crédité</label>
            <select className="form-select" value=${v.dest} onChange=${maj('dest')}>${optionsComptes()}</select>
          </div>` : null}
          <div className="col-12">
            <label className="form-label">${mode === 'transfert' ? 'Note (facultatif)' : 'Libellé'} ${mode === 'transfert' ? null : html`<span className="text-danger">*</span>`}</label>
            <input type="text" className="form-control" value=${v.libelle} onChange=${maj('libelle')} maxLength="200"
                   placeholder=${mode === 'entree' ? 'Ex. : apport du gérant, versement des ventes du jour…'
                     : (mode === 'sortie' ? 'Ex. : retrait pour le salaire, avance…' : 'Ex. : dépôt en banque')} />
          </div>
        </div>
      </form>
    <//>`;
  }

  function CarteCompte(p) {
    var c = p.compte;
    return html`<div className="hx-compte">
      <div className="hx-compte-tete">
        <div className="hx-compte-ico"><${Icone} nom=${ICONES_COMPTE[c.type] || 'wallet2'} /></div>
        <div style=${{ flex: 1, minWidth: 0 }}>
          <div className="hx-compte-nom">${c.nom}</div>
          <div className="hx-compte-sub">${c.type}${c.boutique ? ' · ' + c.boutique : ''}</div>
        </div>
        <button type="button" className="btn btn-sm btn-outline-secondary" title="Archiver ce compte" aria-label="Archiver ce compte"
                onClick=${function () { p.onArchiver(c); }}><${Icone} nom="archive" /></button>
      </div>
      <div className=${'hx-compte-solde' + (c.solde < 0 ? ' negatif' : '')}>${HX.fmtMoney(c.solde)}</div>
      <div className="hx-compte-flux">
        <span>Départ : ${HX.fmtNum(c.solde_initial)}</span>
        <span className="hx-var-haut">+ ${HX.fmtNum(c.entrees)}</span>
        <span className="hx-var-bas">− ${HX.fmtNum(c.sorties)}</span>
      </div>
    </div>`;
  }

  function Tresorerie() {
    var debutEt = useState(HX.isoDebutMois(0));
    var finEt = useState(HX.isoAujourdhui());
    var compteEt = useState('');
    var typeEt = useState('');
    var appliques = useState({ debut: HX.isoDebutMois(0), fin: HX.isoAujourdhui(), compte: '', type: '' });
    var modal = useState(null); // null | 'compte' | 'entree' | 'sortie' | 'transfert'
    var notice = HX.useNotice();

    var a = appliques[0];
    var url = '/api/tresorerie?debut=' + a.debut + '&fin=' + a.fin +
      (a.compte ? '&compte_id=' + a.compte : '') + (a.type ? '&type=' + a.type : '');
    var f = HX.useFetch(url);

    function appliquer(e) {
      if (e) e.preventDefault();
      appliques[1]({ debut: debutEt[0], fin: finEt[0], compte: compteEt[0], type: typeEt[0] });
    }
    function archiver(c) {
      if (!window.confirm('Archiver le compte « ' + c.nom + ' » ?\nIl disparaîtra de la liste (solde actuel : ' + HX.fmtMoney(c.solde) + ').')) return;
      HX.api('/api/tresorerie/comptes/' + c.id + '/archiver', { method: 'POST' }).then(
        function () { notice[1]({ type: 'succes', texte: 'Compte archivé.' }); f.recharger(); },
        function (err) { notice[1]({ type: 'erreur', texte: err.message }); }
      );
    }
    function supprimerMvt(m) {
      var msg = 'Supprimer ce mouvement (' + m.libelle + ', ' + HX.fmtMoney(m.montant) + ') ?' +
        (m.transfert ? '\nLes deux écritures du virement seront supprimées.' : '');
      if (!window.confirm(msg)) return;
      HX.api('/api/tresorerie/mouvements/' + m.id, { method: 'DELETE' }).then(
        function () { notice[1]({ type: 'succes', texte: 'Mouvement supprimé.' }); f.recharger(); },
        function (err) { notice[1]({ type: 'erreur', texte: err.message }); }
      );
    }
    function succes(r) {
      modal[1](null);
      var texte = typeof r === 'string' ? r : 'Opération enregistrée.';
      var avert = r && r.avertissement ? ' ' + r.avertissement : '';
      notice[1]({ type: avert ? 'attention' : 'succes', texte: texte + avert });
      f.recharger();
    }

    if (f.chargement && !f.data) return html`<${HX.Chargement} />`;
    if (f.erreur && !f.data) return html`<${HX.ErreurBox} message=${f.erreur} onRetry=${f.recharger} />`;

    var d = f.data;
    var aDesComptes = d.comptes.length > 0;
    var graphFlux = {
      labels: d.flux.map(function (x) { return HX.fmtJourMois(x.date); }),
      datasets: [
        { label: 'Entrées', data: d.flux.map(function (x) { return x.entrees; }), backgroundColor: 'rgba(25,135,84,.85)', borderRadius: 4 },
        { label: 'Sorties', data: d.flux.map(function (x) { return x.sorties; }), backgroundColor: 'rgba(220,53,69,.85)', borderRadius: 4 }
      ]
    };
    var totalEnc = d.encaissements_ventes.reduce(function (s, x) { return s + x.total; }, 0);

    return html`<div className="hx-page">
      <${HX.Notice} notice=${notice[0]} onClose=${function () { notice[1](null); }} />

      ${!d.boutique_active ? html`<div className="alert alert-info mb-0">
        <${Icone} nom="info-circle" /> Pour créer un compte, choisissez d'abord une boutique active (sélecteur en haut de page).
      </div>` : null}

      <div className="row g-3">
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Trésorerie totale" icone="safe2" couleur=${d.total_tresorerie >= 0 ? '' : 'rouge'}
            valeur=${HX.fmtNum(d.total_tresorerie)} unite="FCFA" sous=${d.comptes.length + ' compte(s)'} />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Entrées de la période" icone="box-arrow-in-down" couleur="vert" valeur=${HX.fmtNum(d.entrees_periode)} unite="FCFA" sous="Hors virements entre comptes" />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Sorties de la période" icone="box-arrow-up" couleur="rouge" valeur=${HX.fmtNum(d.sorties_periode)} unite="FCFA" sous="Hors virements entre comptes" />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Solde de la période" icone="arrow-left-right" couleur=${d.solde_periode >= 0 ? 'vert' : 'rouge'}
            valeur=${HX.fmtNum(d.solde_periode)} unite="FCFA" sous=${'Du ' + HX.fmtDate(d.debut) + ' au ' + HX.fmtDate(d.fin)} />
        </div>
      </div>

      <${Carte} titre="Comptes" icone="wallet2"
        actions=${html`<div className="d-flex flex-wrap gap-2">
          <button type="button" className="btn btn-sm btn-success" disabled=${!aDesComptes} onClick=${function () { modal[1]('entree'); }}><${Icone} nom="plus-circle" /> Entrée</button>
          <button type="button" className="btn btn-sm btn-danger" disabled=${!aDesComptes} onClick=${function () { modal[1]('sortie'); }}><${Icone} nom="dash-circle" /> Sortie</button>
          <button type="button" className="btn btn-sm btn-primary" disabled=${d.comptes.length < 2} onClick=${function () { modal[1]('transfert'); }}><${Icone} nom="arrow-left-right" /> Virement</button>
          <button type="button" className="btn btn-sm btn-outline-primary" disabled=${!d.boutique_active} onClick=${function () { modal[1]('compte'); }}><${Icone} nom="plus-lg" /> Nouveau compte</button>
        </div>`}>
        ${aDesComptes ? html`<div className="row g-3">
          ${d.comptes.map(function (c) {
            return html`<div className="col-md-6 col-xl-4" key=${c.id}><${CarteCompte} compte=${c} onArchiver=${archiver} /></div>`;
          })}
        </div>` : html`<${Vide} icone="wallet2" texte="Aucun compte. Créez par exemple une « Caisse principale » avec son solde de départ." />`}
      <//>

      <div className="row g-3">
        <div className="col-lg-8">
          <${Carte} titre="Flux de la période" icone="bar-chart-line">
            ${d.flux.length
              ? html`<${Graphique} type="bar" data=${graphFlux} legende=${true} taille="petit"
                  options=${{ scales: { y: HX.AXE_ARGENT, x: { grid: { display: false } } }, plugins: { tooltip: HX.INFO_BULLE_ARGENT } }} />`
              : html`<${Vide} icone="bar-chart-line" texte="Aucun mouvement sur cette période" />`}
          <//>
        </div>
        <div className="col-lg-4">
          <${Carte} titre="Ventes encaissées (même période)" icone="cash-coin">
            ${d.encaissements_ventes.length ? html`<div className="hx-table-wrap"><table className="hx-table">
              <tbody>
              ${d.encaissements_ventes.map(function (x) {
                return html`<tr key=${x.mode}><td>${x.mode}</td><td className="num">${HX.fmtMoney(x.total)}</td></tr>`;
              })}
              </tbody>
              <tfoot><tr><td>Total</td><td className="num">${HX.fmtMoney(totalEnc)}</td></tr></tfoot>
            </table></div>
            <div className="text-muted small mt-2">À titre d'information : pour rapprocher avec vos comptes, enregistrez les versements dans une caisse via « Entrée ».</div>`
              : html`<${Vide} icone="cash-coin" texte="Aucune vente sur la période" />`}
          <//>
        </div>
      </div>

      <${Carte} titre="Mouvements" icone="journal-text">
        <form onSubmit=${appliquer} className="row g-2 align-items-end mb-3">
          <div className="col-6 col-md-auto">
            <label className="form-label mb-1">Du</label>
            <${HX.DateField} value=${debutEt[0]} onChange=${debutEt[1]} label="Date de début" />
          </div>
          <div className="col-6 col-md-auto">
            <label className="form-label mb-1">Au</label>
            <${HX.DateField} value=${finEt[0]} onChange=${finEt[1]} label="Date de fin" />
          </div>
          <div className="col-6 col-md-3">
            <label className="form-label mb-1">Compte</label>
            <select className="form-select" value=${compteEt[0]} onChange=${function (e) { compteEt[1](e.target.value); }}>
              <option value="">Tous les comptes</option>
              ${d.comptes.map(function (c) { return html`<option key=${c.id} value=${c.id}>${c.nom}</option>`; })}
            </select>
          </div>
          <div className="col-6 col-md-2">
            <label className="form-label mb-1">Type</label>
            <select className="form-select" value=${typeEt[0]} onChange=${function (e) { typeEt[1](e.target.value); }}>
              <option value="">Tous</option>
              <option value="entree">Entrées</option>
              <option value="sortie">Sorties</option>
            </select>
          </div>
          <div className="col-12 col-md-auto">
            <button type="submit" className="btn btn-primary"><${Icone} nom="funnel" /> Filtrer</button>
          </div>
        </form>
        ${d.mouvements.length ? html`<div className="hx-table-wrap"><table className="hx-table">
          <thead><tr><th>Date</th><th>Compte</th><th>Libellé</th><th>Origine</th><th className="num">Entrée</th><th className="num">Sortie</th><th></th></tr></thead>
          <tbody>
          ${d.mouvements.map(function (m) {
            var origine = m.source === 'depense' ? ['Dépense', 'orange'] : (m.source === 'transfert' ? ['Virement', ''] : ['Manuel', 'gris']);
            return html`<tr key=${m.id}>
              <td>${HX.fmtDate(m.date)}</td>
              <td>${m.compte}</td>
              <td>${m.libelle}${m.auteur ? html`<div className="text-muted small">${m.auteur}</div>` : null}</td>
              <td><span className=${'hx-badge ' + origine[1]}>${origine[0]}</span></td>
              <td className="num hx-var-haut">${m.type === 'entree' ? HX.fmtNum(m.montant) : ''}</td>
              <td className="num hx-var-bas">${m.type === 'sortie' ? HX.fmtNum(m.montant) : ''}</td>
              <td className="num">
                ${m.source === 'depense' ? html`<a className="btn btn-sm btn-outline-secondary" href="/admin/depenses" title="Gérer depuis la page Dépenses"><${Icone} nom="box-arrow-up-right" /></a>`
                  : html`<button type="button" className="btn btn-sm btn-outline-danger" title="Supprimer" aria-label="Supprimer le mouvement"
                           onClick=${function () { supprimerMvt(m); }}><${Icone} nom="trash3" /></button>`}
              </td>
            </tr>`;
          })}
          </tbody></table></div>` : html`<${Vide} icone="journal-text" texte="Aucun mouvement sur cette période" />`}
      <//>

      ${modal[0] === 'compte' ? html`<${FormCompte} types=${d.types_compte} onClose=${function () { modal[1](null); }} onSucces=${succes} />` : null}
      ${(modal[0] === 'entree' || modal[0] === 'sortie' || modal[0] === 'transfert')
        ? html`<${FormMouvement} mode=${modal[0]} comptes=${d.comptes} onClose=${function () { modal[1](null); }} onSucces=${succes} />` : null}
    </div>`;
  }

  HX.monter(Tresorerie);
})();
