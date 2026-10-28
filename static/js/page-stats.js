/* Statistiques — analyse des ventes et de la rentabilité (React, données : /api/stats) */
(function () {
  'use strict';
  var HX = window.HX, html = HX.html, useState = HX.useState;
  var Kpi = HX.Kpi, Carte = HX.Carte, Icone = HX.Icone, Vide = HX.Vide, Graphique = HX.Graphique;

  var PERIODES = [
    { jours: 7, libelle: '7 jours' },
    { jours: 30, libelle: '30 jours' },
    { jours: 90, libelle: '90 jours' },
    { jours: 365, libelle: '12 mois' }
  ];

  function libelleCle(cle, granularite) {
    if (granularite === 'mois') {
      var p = cle.split('-');
      return p[1] + '/' + p[0];
    }
    return HX.fmtJourMois(cle);
  }

  function Classement(p) {
    var max = Math.max.apply(null, p.lignes.map(function (l) { return l.total; }).concat([1]));
    return html`<div className="hx-table-wrap"><table className="hx-table">
      <thead><tr><th>${p.entete}</th><th className="num">${p.colonneQte || 'Quantité'}</th><th className="num">Chiffre d'affaires</th></tr></thead>
      <tbody>
      ${p.lignes.map(function (l, i) {
        return html`<tr key=${l.nom + i}>
          <td style=${{ width: '55%' }}>
            <div><strong>${i + 1}.</strong> ${l.nom}</div>
            <div className="hx-barre-prog"><div style=${{ width: Math.max(3, l.total / max * 100) + '%' }}></div></div>
          </td>
          <td className="num">${HX.fmtQte(l.quantite !== undefined ? l.quantite : l.nb)}</td>
          <td className="num">${HX.fmtMoney(l.total)}</td>
        </tr>`;
      })}
      </tbody></table></div>`;
  }

  function Statistiques() {
    var etatPeriode = useState(30);
    var jours = etatPeriode[0];
    var f = HX.useFetch('/api/stats?jours=' + jours);

    var selecteur = html`<div className="hx-barre">
      <div className="hx-seg" role="group" aria-label="Période">
        ${PERIODES.map(function (p) {
          return html`<button key=${p.jours} type="button" className=${p.jours === jours ? 'actif' : ''}
                      onClick=${function () { etatPeriode[1](p.jours); }}>${p.libelle}</button>`;
        })}
      </div>
      ${f.data ? html`<div className="text-muted">
        <${Icone} nom="calendar-range" /> Du ${HX.fmtDate(f.data.debut)} au ${HX.fmtDate(f.data.fin)}
      </div>` : null}
      ${f.data ? html`<a href=${'/export/pdf_stats?jours=' + jours} target="_blank"
          style=${{ marginLeft: 'auto', background: '#1e3c72', color: 'white', padding: '8px 14px', borderRadius: '10px', textDecoration: 'none', fontWeight: 'bold' }}>
          <${Icone} nom="file-earmark-pdf" /> Exporter en PDF</a>` : null}
    </div>`;

    if (f.chargement && !f.data) return html`<div className="hx-page">${selecteur}<${HX.Chargement} /></div>`;
    if (f.erreur && !f.data) return html`<div className="hx-page">${selecteur}<${HX.ErreurBox} message=${f.erreur} onRetry=${f.recharger} /></div>`;

    var d = f.data, k = d.kpis;

    var graphSerie = {
      labels: d.serie.map(function (s) { return libelleCle(s.cle, d.granularite); }),
      datasets: [{
        label: 'Ventes', data: d.serie.map(function (s) { return s.total; }),
        borderColor: '#2a5298', backgroundColor: 'rgba(42,82,152,.12)', fill: true, tension: .25,
        pointRadius: d.serie.length > 40 ? 0 : 3
      }]
    };
    var graphSemaine = {
      labels: d.jours_semaine.map(function (j) { return j.jour; }),
      datasets: [{ label: 'Ventes', data: d.jours_semaine.map(function (j) { return j.total; }), backgroundColor: '#2a5298', borderRadius: 6 }]
    };
    var graphHeures = {
      labels: d.par_heure.slice(6, 23).map(function (h) { return h.heure + ' h'; }),
      datasets: [{ label: 'Ventes', data: d.par_heure.slice(6, 23).map(function (h) { return h.total; }), backgroundColor: '#198754', borderRadius: 6 }]
    };
    var graphModes = {
      labels: d.modes_paiement.map(function (m) { return m.mode; }),
      datasets: [{ data: d.modes_paiement.map(function (m) { return m.total; }), backgroundColor: HX.PALETTE, borderWidth: 2 }]
    };
    var graphMensuel = {
      labels: d.mensuel.map(function (m) { return m.libelle; }),
      datasets: [
        { type: 'bar', label: 'Ventes', data: d.mensuel.map(function (m) { return m.ventes; }), backgroundColor: 'rgba(42,82,152,.85)', borderRadius: 4 },
        { type: 'bar', label: 'Achats de stock', data: d.mensuel.map(function (m) { return m.achats; }), backgroundColor: 'rgba(245,159,0,.85)', borderRadius: 4 },
        { type: 'bar', label: 'Charges', data: d.mensuel.map(function (m) { return m.charges; }), backgroundColor: 'rgba(220,53,69,.8)', borderRadius: 4 },
        { type: 'line', label: 'Résultat', data: d.mensuel.map(function (m) { return m.resultat; }), borderColor: '#198754', backgroundColor: '#198754', tension: .25, pointRadius: 3 }
      ]
    };
    var optionsArgent = { scales: { y: Object.assign({}, HX.AXE_ARGENT, { beginAtZero: false }), x: { grid: { display: false } } }, plugins: { tooltip: HX.INFO_BULLE_ARGENT } };
    var optionsBarres = { scales: { y: HX.AXE_ARGENT, x: { grid: { display: false } } }, plugins: { tooltip: HX.INFO_BULLE_ARGENT } };

    return html`<div className="hx-page">
      ${selecteur}
      ${f.erreur ? html`<${HX.ErreurBox} message=${f.erreur} onRetry=${f.recharger} />` : null}

      <div className="row g-3">
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Chiffre d'affaires" icone="cash-coin" couleur="vert" valeur=${HX.fmtNum(k.ca)} unite="FCFA"
            sous=${html`<${HX.Variation} valeur=${k.variation_ca} /> vs période précédente`} />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Nombre de ventes" icone="receipt" valeur=${HX.fmtNum(k.nb_ventes)}
            sous=${html`<${HX.Variation} valeur=${k.variation_nb} /> · ${HX.fmtQte(k.nb_articles)} article(s)`} />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Panier moyen" icone="basket" valeur=${HX.fmtNum(k.panier_moyen)} unite="FCFA" sous="Par vente" />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Résultat" icone="graph-up-arrow" couleur=${k.resultat >= 0 ? 'vert' : 'rouge'}
            valeur=${HX.fmtNum(k.resultat)} unite="FCFA" sous="Ventes − achats de stock − charges" />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Achats de stock" icone="box-arrow-in-down" couleur="orange" valeur=${HX.fmtNum(k.achats)} unite="FCFA"
            sous=${'Marge brute : ' + HX.fmtMoney(k.marge_brute)} />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Charges" icone="cash-stack" couleur="rouge" valeur=${HX.fmtNum(k.charges)} unite="FCFA" sous="Loyer, salaires, factures…" />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Dépenses enregistrées" icone="receipt" couleur="orange" valeur=${HX.fmtNum(k.depenses)} unite="FCFA" sous="Page Dépenses" />
        </div>
        <div className="col-6 col-lg-3">
          <${Kpi} titre="Pertes" icone="trash3" couleur="gris" valeur=${HX.fmtNum(k.pertes)} unite="FCFA" sous="Casse, vol, péremption" />
        </div>
      </div>

      <${Carte} titre=${'Évolution des ventes (' + (d.granularite === 'mois' ? 'par mois' : 'par jour') + ')'} icone="graph-up">
        <${Graphique} type="line" data=${graphSerie} taille="grand" options=${optionsArgent} />
      <//>

      <div className="row g-3">
        <div className="col-lg-6">
          <${Carte} titre="Ventes par jour de la semaine" icone="calendar-week">
            <${Graphique} type="bar" data=${graphSemaine} taille="petit" options=${optionsBarres} />
          <//>
        </div>
        <div className="col-lg-6">
          <${Carte} titre="Ventes par heure" icone="clock-history">
            <${Graphique} type="bar" data=${graphHeures} taille="petit" options=${optionsBarres} />
          <//>
        </div>
      </div>

      <div className="row g-3">
        <div className="col-lg-7">
          <${Carte} titre="Meilleurs produits" icone="trophy">
            ${d.top_produits.length ? html`<${Classement} entete="Produit" lignes=${d.top_produits} />`
              : html`<${Vide} icone="trophy" texte="Aucune vente sur la période" />`}
          <//>
        </div>
        <div className="col-lg-5">
          <${Carte} titre="Modes de paiement" icone="wallet2">
            ${d.modes_paiement.length
              ? html`<${Graphique} type="doughnut" data=${graphModes} legende=${true} taille="petit" options=${{ plugins: { tooltip: HX.INFO_BULLE_ARGENT } }} />`
              : html`<${Vide} icone="wallet2" texte="Aucune vente sur la période" />`}
          <//>
        </div>
      </div>

      <div className="row g-3">
        <div className="col-lg-6">
          <${Carte} titre="Performance des vendeurs" icone="people">
            ${d.vendeurs.length ? html`<${Classement} entete="Vendeur" colonneQte="Ventes" lignes=${d.vendeurs} />`
              : html`<${Vide} icone="people" texte="Aucune vente sur la période" />`}
          <//>
        </div>
        <div className="col-lg-6">
          <${Carte} titre="Produits en stock sans vente sur la période" icone="hourglass-split">
            ${d.produits_dormants.length ? html`<div className="hx-table-wrap"><table className="hx-table">
              <thead><tr><th>Produit</th><th className="num">Stock</th><th className="num">Valeur immobilisée</th></tr></thead>
              <tbody>
              ${d.produits_dormants.map(function (p, i) {
                return html`<tr key=${p.nom + i}><td>${p.nom}</td><td className="num">${HX.fmtQte(p.stock)}</td><td className="num">${HX.fmtMoney(p.valeur)}</td></tr>`;
              })}
              </tbody></table></div>`
              : html`<${Vide} icone="check-circle" texte="Tous les produits en stock se sont vendus" />`}
          <//>
        </div>
      </div>

      <div className="row g-3">
        <div className="col-lg-6">
          <${Carte} titre="Dépenses par catégorie" icone="receipt">
            ${d.depenses_par_categorie.length ? html`<div className="hx-table-wrap"><table className="hx-table"><tbody>
              ${d.depenses_par_categorie.map(function (c) {
                return html`<tr key=${c.categorie}><td>${c.categorie}</td><td className="num">${HX.fmtMoney(c.total)}</td></tr>`;
              })}
              </tbody></table></div>` : html`<${Vide} icone="receipt" texte="Aucune dépense sur la période" />`}
          <//>
        </div>
        <div className="col-lg-6">
          <${Carte} titre="Charges par catégorie" icone="cash-stack">
            ${d.charges_par_categorie.length ? html`<div className="hx-table-wrap"><table className="hx-table"><tbody>
              ${d.charges_par_categorie.map(function (c) {
                return html`<tr key=${c.categorie}><td>${c.categorie}</td><td className="num">${HX.fmtMoney(c.total)}</td></tr>`;
              })}
              </tbody></table></div>` : html`<${Vide} icone="cash-stack" texte="Aucune charge sur la période" />`}
          <//>
        </div>
      </div>

      <${Carte} titre="Résultat mensuel (12 derniers mois)" icone="bar-chart-steps"
        actions=${html`<a className="btn btn-sm btn-outline-primary" href="/admin/comptabilite">Tableau comptable</a>`}>
        <${Graphique} type="bar" data=${graphMensuel} legende=${true} taille="grand" options=${optionsArgent} />
        <div className="hx-table-wrap mt-3"><table className="hx-table">
          <thead><tr><th>Mois</th><th className="num">Ventes</th><th className="num">Achats</th><th className="num">Charges</th><th className="num">Résultat</th><th className="num">Dépenses enreg.</th></tr></thead>
          <tbody>
          ${d.mensuel.slice().reverse().map(function (m) {
            return html`<tr key=${m.cle}>
              <td>${m.libelle}</td><td className="num">${HX.fmtNum(m.ventes)}</td><td className="num">${HX.fmtNum(m.achats)}</td>
              <td className="num">${HX.fmtNum(m.charges)}</td>
              <td className=${'num ' + (m.resultat >= 0 ? 'hx-var-haut' : 'hx-var-bas')}>${HX.fmtNum(m.resultat)}</td>
              <td className="num">${HX.fmtNum(m.depenses)}</td>
            </tr>`;
          })}
          </tbody></table></div>
      <//>
    </div>`;
  }

  HX.monter(Statistiques);
})();