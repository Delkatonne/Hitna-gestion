/* Dashboard administrateur — vue d'ensemble (React, données : /api/dashboard) */
(function () {
  'use strict';
  var HX = window.HX, html = HX.html, useEffect = HX.useEffect;
  var Kpi = HX.Kpi, Carte = HX.Carte, Icone = HX.Icone, Vide = HX.Vide, Graphique = HX.Graphique;

  function Alerte(p) {
    return html`<a className=${'hx-alerte ' + (p.niveau || '')} href=${p.lien}>
      <span className="hx-pastille"><${Icone} nom=${p.icone} /></span>
      <div><strong>${p.valeur}</strong><span className="lib">${p.libelle}</span></div>
    </a>`;
  }

  function LigneResume(p) {
    return html`<tr>
      <td>${p.libelle}</td>
      <td className=${'num' + (p.couleur ? ' ' + p.couleur : '')}>${p.signe || ''}${HX.fmtMoney(p.valeur)}</td>
    </tr>`;
  }

  function Dashboard() {
    var f = HX.useFetch('/api/dashboard');

    // Actualisation automatique toutes les 2 minutes
    useEffect(function () {
      var t = setInterval(f.recharger, 120000);
      return function () { clearInterval(t); };
    }, []);

    if (f.chargement && !f.data) return html`<${HX.Chargement} />`;
    if (f.erreur && !f.data) return html`<${HX.ErreurBox} message=${f.erreur} onRetry=${f.recharger} />`;

    var d = f.data, k = d.kpis;
    var maxTop = Math.max.apply(null, d.top_produits.map(function (p) { return p.total; }).concat([1]));
    var totalComptes = d.comptes.length;

    var graphVentes = {
      labels: d.ventes_14_jours.map(function (j) { return HX.fmtJourMois(j.date); }),
      datasets: [{
        label: 'Ventes', data: d.ventes_14_jours.map(function (j) { return j.total; }),
        backgroundColor: 'rgba(42,82,152,.85)', borderRadius: 6
      }]
    };
    var graphHeures = {
      labels: d.ventes_par_heure.slice(6, 23).map(function (h) { return h.heure + ' h'; }),
      datasets: [{
        label: 'Ventes', data: d.ventes_par_heure.slice(6, 23).map(function (h) { return h.total; }),
        borderColor: '#198754', backgroundColor: 'rgba(25,135,84,.15)', fill: true, tension: .3, pointRadius: 3
      }]
    };
    var graphModes = {
      labels: d.modes_paiement.map(function (m) { return m.mode; }),
      datasets: [{ data: d.modes_paiement.map(function (m) { return m.total; }), backgroundColor: HX.PALETTE, borderWidth: 2 }]
    };
    var graphDepenses = {
      labels: d.depenses_par_categorie.map(function (c) { return c.categorie; }),
      datasets: [{ data: d.depenses_par_categorie.map(function (c) { return c.total; }), backgroundColor: HX.PALETTE, borderWidth: 2 }]
    };

    var aDesAlertes = k.nb_ruptures > 0 || k.nb_stock_bas > 0 || k.commandes_nouvelles > 0 || k.messages_non_lus > 0;

    return html`<div className="hx-page">

      <div className="hx-barre">
        <div className="text-muted">
          <${Icone} nom="calendar3" /> Données au ${HX.fmtDate(d.date)}
        </div>
        <div className="hx-groupe">
          <a className="btn btn-primary btn-sm" href="/admin/ventes"><${Icone} nom="cart-plus" /> Nouvelle vente</a>
          <a className="btn btn-outline-primary btn-sm" href="/admin/depenses"><${Icone} nom="receipt" /> Nouvelle dépense</a>
          <a className="btn btn-outline-primary btn-sm" href="/entrees"><${Icone} nom="box-arrow-in-down" /> Entrée de stock</a>
          <button className="btn btn-outline-secondary btn-sm" onClick=${f.recharger} disabled=${f.chargement}>
            <${Icone} nom="arrow-clockwise" /> Actualiser
          </button>
        </div>
      </div>

      ${f.erreur ? html`<${HX.ErreurBox} message=${f.erreur} onRetry=${f.recharger} />` : null}

      <div className="row g-3">
        <div className="col-6 col-lg-4 col-xl-2">
          <${Kpi} titre="Ventes du jour" icone="cash-coin" couleur="vert" valeur=${HX.fmtNum(k.ventes_jour)} unite="FCFA"
            sous=${html`${k.nb_ventes_jour} vente(s) · <${HX.Variation} valeur=${k.variation_jour} /> vs hier`} />
        </div>
        <div className="col-6 col-lg-4 col-xl-2">
          <${Kpi} titre="Bénéfice brut du jour" icone="graph-up-arrow" couleur="vert" valeur=${HX.fmtNum(d.benefices.jour.benefice)} unite="FCFA"
            sous=${d.benefices.jour.marge_pct !== null ? 'Marge ' + d.benefices.jour.marge_pct + ' %' : 'Aucune vente aujourd\'hui'} />
        </div>
        <div className="col-6 col-lg-4 col-xl-2">
          <${Kpi} titre="Ventes du mois" icone="calendar-month" valeur=${HX.fmtNum(k.ventes_mois)} unite="FCFA"
            sous=${html`${k.nb_ventes_mois} vente(s) · <${HX.Variation} valeur=${k.variation_mois} /> vs même période du mois dernier`} />
        </div>
        <div className="col-6 col-lg-4 col-xl-2">
          <${Kpi} titre="Bénéfice net du mois" icone="graph-up-arrow" couleur=${k.benefice_net_mois >= 0 ? 'vert' : 'rouge'}
            valeur=${HX.fmtNum(k.benefice_net_mois)} unite="FCFA" sous="Ventes − achats de stock − charges" />
        </div>
        <div className="col-6 col-lg-4 col-xl-2">
          <${Kpi} titre="Dépenses du mois" icone="receipt" couleur="orange" valeur=${HX.fmtNum(k.depenses_mois)} unite="FCFA"
            sous=${'Charges du mois : ' + HX.fmtMoney(k.charges_mois)} />
        </div>
        <div className="col-6 col-lg-4 col-xl-2">
          <${Kpi} titre="Trésorerie" icone="safe2" couleur=${k.tresorerie_totale >= 0 ? '' : 'rouge'} valeur=${HX.fmtNum(k.tresorerie_totale)} unite="FCFA"
            sous=${totalComptes ? totalComptes + ' compte(s)' : 'Aucun compte créé'} />
        </div>
        <div className="col-6 col-lg-4 col-xl-2">
          <${Kpi} titre="Crédits clients" icone="person-lines-fill" couleur="gris" valeur=${HX.fmtNum(k.credits_en_cours)} unite="FCFA"
            sous=${k.nb_credits + ' crédit(s) en cours'} />
        </div>
      </div>

      ${aDesAlertes ? html`<div className="row g-3">
        ${k.nb_ruptures > 0 ? html`<div className="col-6 col-lg-3"><${Alerte} niveau="danger" icone="exclamation-octagon" lien="/admin/alertes/produits" valeur=${k.nb_ruptures} libelle="Produit(s) en rupture" /></div>` : null}
        ${k.nb_stock_bas > 0 ? html`<div className="col-6 col-lg-3"><${Alerte} niveau="attention" icone="box-seam" lien="/admin/reapprovisionnement" valeur=${k.nb_stock_bas} libelle="Produit(s) sous le stock minimum" /></div>` : null}
        ${k.commandes_nouvelles > 0 ? html`<div className="col-6 col-lg-3"><${Alerte} icone="bag-check" lien="/admin/commandes" valeur=${k.commandes_nouvelles} libelle="Nouvelle(s) commande(s)" /></div>` : null}
        ${k.messages_non_lus > 0 ? html`<div className="col-6 col-lg-3"><${Alerte} icone="envelope" lien="/admin/messages" valeur=${k.messages_non_lus} libelle="Message(s) non lu(s)" /></div>` : null}
      </div>` : null}

      <div className="row g-3">
        <div className="col-12">
          <${Carte} titre="Bénéfice par produit (mois en cours)" icone="graph-up-arrow">
            ${d.benefices.par_produit.length
              ? html`<div className="table-responsive"><table className="table table-sm align-middle mb-0">
                  <thead><tr><th>Produit</th><th className="text-end">Ventes (FCFA)</th><th className="text-end">Coût (FCFA)</th><th className="text-end">Bénéfice (FCFA)</th><th className="text-end">Marge</th></tr></thead>
                  <tbody>${d.benefices.par_produit.map(function (p) {
                    return html`<tr key=${p.nom}><td>${p.nom}</td><td className="text-end">${HX.fmtNum(p.ca)}</td><td className="text-end">${HX.fmtNum(p.cout)}</td><td className="text-end">${HX.fmtNum(p.benefice)}</td><td className="text-end">${p.marge_pct === null ? '-' : p.marge_pct + ' %'}</td></tr>`;
                  })}</tbody></table></div>`
              : html`<${Vide} icone="graph-up-arrow" texte="Aucune vente ce mois-ci" />`}
          <//>
        </div>
      </div>

      <div className="row g-3">
        <div className="col-lg-8">
          <${Carte} titre="Ventes des 14 derniers jours" icone="bar-chart-line">
            <${Graphique} type="bar" data=${graphVentes}
              options=${{ scales: { y: HX.AXE_ARGENT, x: { grid: { display: false } } }, plugins: { tooltip: HX.INFO_BULLE_ARGENT } }} />
          <//>
        </div>
        <div className="col-lg-4">
          <${Carte} titre="Modes de paiement (mois)" icone="wallet2">
            ${d.modes_paiement.length
              ? html`<${Graphique} type="doughnut" data=${graphModes} legende=${true} options=${{ plugins: { tooltip: HX.INFO_BULLE_ARGENT } }} />`
              : html`<${Vide} icone="wallet2" texte="Aucune vente ce mois-ci" />`}
          <//>
        </div>
      </div>

      <div className="row g-3">
        <div className="col-lg-6">
          <${Carte} titre="Ventes par heure (aujourd'hui)" icone="clock-history">
            <${Graphique} type="line" data=${graphHeures} taille="petit"
              options=${{ scales: { y: HX.AXE_ARGENT, x: { grid: { display: false } } }, plugins: { tooltip: HX.INFO_BULLE_ARGENT } }} />
          <//>
        </div>
        <div className="col-lg-6">
          <${Carte} titre="Meilleurs produits (30 jours)" icone="trophy">
            ${d.top_produits.length ? html`<div className="hx-table-wrap"><table className="hx-table"><tbody>
              ${d.top_produits.map(function (p, i) {
                return html`<tr key=${p.nom + i}>
                  <td style=${{ width: '70%' }}>
                    <div><strong>${i + 1}.</strong> ${p.nom}</div>
                    <div className="hx-barre-prog"><div style=${{ width: Math.max(4, p.total / maxTop * 100) + '%' }}></div></div>
                  </td>
                  <td className="num">${HX.fmtMoney(p.total)}<div className="text-muted small">${HX.fmtQte(p.quantite)} vendu(s)</div></td>
                </tr>`;
              })}
            </tbody></table></div>` : html`<${Vide} icone="trophy" texte="Pas encore de ventes sur 30 jours" />`}
          <//>
        </div>
      </div>

      <div className="row g-3">
        <div className="col-lg-4">
          <${Carte} titre="Résumé du mois" icone="journal-text"
            actions=${html`<a className="btn btn-sm btn-outline-primary" href="/admin/comptabilite">Détail</a>`}>
            <div className="hx-table-wrap"><table className="hx-table"><tbody>
              <${LigneResume} libelle="Ventes" valeur=${k.ventes_mois} couleur="hx-var-haut" />
              <${LigneResume} libelle="Achats de stock" valeur=${k.achats_mois} signe="− " />
              <${LigneResume} libelle="Charges" valeur=${k.charges_mois} signe="− " />
              <${LigneResume} libelle="Bénéfice net" valeur=${k.benefice_net_mois} couleur=${k.benefice_net_mois >= 0 ? 'hx-var-haut' : 'hx-var-bas'} />
              <${LigneResume} libelle="Dépenses enregistrées" valeur=${k.depenses_mois} />
              <${LigneResume} libelle="Pertes (valeur)" valeur=${k.pertes_mois} />
              <${LigneResume} libelle="Valeur du stock (prix de vente)" valeur=${k.valeur_stock} />
            </tbody></table></div>
          <//>
        </div>
        <div className="col-lg-4">
          <${Carte} titre="Comptes de trésorerie" icone="safe2"
            actions=${html`<a className="btn btn-sm btn-outline-primary" href="/admin/tresorerie">Gérer</a>`}>
            ${d.comptes.length ? html`<div className="hx-table-wrap"><table className="hx-table"><tbody>
              ${d.comptes.map(function (c) {
                return html`<tr key=${c.id}>
                  <td>${c.nom}<div className="text-muted small">${c.type}${c.boutique ? ' · ' + c.boutique : ''}</div></td>
                  <td className=${'num ' + (c.solde < 0 ? 'hx-var-bas' : '')}>${HX.fmtMoney(c.solde)}</td>
                </tr>`;
              })}
            </tbody></table></div>` : html`<${Vide} icone="safe2" texte="Aucun compte. Créez une caisse depuis la page Trésorerie." />`}
          <//>
        </div>
        <div className="col-lg-4">
          <${Carte} titre="Dépenses par catégorie (mois)" icone="pie-chart">
            ${d.depenses_par_categorie.length
              ? html`<${Graphique} type="doughnut" data=${graphDepenses} legende=${true} taille="petit" options=${{ plugins: { tooltip: HX.INFO_BULLE_ARGENT } }} />`
              : html`<${Vide} icone="receipt" texte="Aucune dépense ce mois-ci" />`}
          <//>
        </div>
      </div>

      <div className="row g-3">
        <div className="col-lg-7">
          <${Carte} titre="Dernières ventes" icone="receipt-cutoff"
            actions=${html`<a className="btn btn-sm btn-outline-primary" href="/admin/recus">Tous les reçus</a>`}>
            ${d.dernieres_ventes.length ? html`<div className="hx-table-wrap"><table className="hx-table">
              <thead><tr><th>Date</th><th>Client</th><th>Vendeur</th><th>Paiement</th><th className="num">Total</th></tr></thead>
              <tbody>
              ${d.dernieres_ventes.map(function (v) {
                return html`<tr key=${v.groupe}>
                  <td>${HX.fmtDateHeure(v.date)}</td><td>${v.client || '-'}</td><td>${v.vendeur || '-'}</td>
                  <td><span className="hx-badge gris">${v.mode}</span></td>
                  <td className="num"><a href=${'/vente/recu/' + v.groupe}>${HX.fmtMoney(v.total)}</a></td>
                </tr>`;
              })}
              </tbody></table></div>` : html`<${Vide} icone="receipt-cutoff" texte="Aucune vente sur les 7 derniers jours" />`}
          <//>
        </div>
        <div className="col-lg-5">
          <${Carte} titre="Stock à surveiller" icone="exclamation-triangle"
            actions=${html`<a className="btn btn-sm btn-outline-primary" href="/admin/reapprovisionnement">Réapprovisionner</a>`}>
            ${d.stock_bas.length ? html`<div className="hx-table-wrap"><table className="hx-table">
              <thead><tr><th>Produit</th><th className="num">Stock</th><th className="num">Minimum</th></tr></thead>
              <tbody>
              ${d.stock_bas.map(function (p, i) {
                return html`<tr key=${p.nom + i}>
                  <td>${p.nom}</td>
                  <td className="num"><span className=${'hx-badge ' + (p.stock <= 0 ? 'rouge' : 'orange')}>${HX.fmtQte(p.stock)}</span></td>
                  <td className="num">${HX.fmtQte(p.stock_min)}</td>
                </tr>`;
              })}
              </tbody></table></div>` : html`<${Vide} icone="check-circle" texte="Tous les produits sont au-dessus du stock minimum" />`}
          <//>
        </div>
      </div>

      <div className="row g-3">
        <div className="col-12">
          <${Carte} titre="Vendeurs du jour" icone="people">
            ${d.vendeurs.length ? html`<div className="hx-table-wrap"><table className="hx-table">
              <thead><tr><th>Vendeur</th><th className="num">Ventes</th><th className="num">Total</th></tr></thead>
              <tbody>
              ${d.vendeurs.map(function (v, i) {
                return html`<tr key=${v.nom + i}><td>${v.nom}</td><td className="num">${v.nb}</td><td className="num">${HX.fmtMoney(v.total)}</td></tr>`;
              })}
              </tbody></table></div>` : html`<${Vide} icone="people" texte="Aucune vente enregistrée aujourd'hui" />`}
          <//>
        </div>
      </div>
    </div>`;
  }

  HX.monter(Dashboard);
})();