/* HITNA — affiche toutes les dates des pages au format jj/mm/aaaa.
   Les dates restent en aaaa-mm-jj dans la base, dans les formulaires et dans les URL ;
   ce script ne remplace que le TEXTE affiché (jamais les champs de saisie, les scripts
   ni les zones marquées data-no-datefr, comme les pages React qui formatent déjà). */
(function () {
  'use strict';
  var RE = /\b(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?)?(?!\d)/g;
  var IGNORES = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, INPUT: 1, SELECT: 0, NOSCRIPT: 1, CODE: 0 };

  function convertir(texte) {
    return texte.replace(RE, function (tout, a, m, j, h) {
      var mo = +m, jo = +j;
      if (mo < 1 || mo > 12 || jo < 1 || jo > 31) return tout;
      return j + '/' + m + '/' + a + (h ? ' ' + h : '');
    });
  }

  function ignore(noeud) {
    for (var el = noeud.parentNode; el && el.nodeType === 1; el = el.parentNode) {
      if (IGNORES[el.nodeName] === 1) return true;
      if (el.hasAttribute && el.hasAttribute('data-no-datefr')) return true;
      if (el.isContentEditable) return true;
    }
    return false;
  }

  function traiterTexte(noeud) {
    var v = noeud.nodeValue;
    if (!v || v.indexOf('-') === -1) return;
    RE.lastIndex = 0;
    if (!RE.test(v)) return;
    RE.lastIndex = 0;
    if (ignore(noeud)) return;
    var nv = convertir(v);
    if (nv !== v) noeud.nodeValue = nv;
  }

  function parcourir(racine) {
    if (racine.nodeType === 3) { traiterTexte(racine); return; }
    if (racine.nodeType !== 1 || IGNORES[racine.nodeName] === 1) return;
    var w = document.createTreeWalker(racine, NodeFilter.SHOW_TEXT, null);
    var n;
    while ((n = w.nextNode())) traiterTexte(n);
  }

  function demarrer() {
    if (!document.body) return;
    parcourir(document.body);
    var timer = null, file = [];
    new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        if (m.type === 'childList') {
          for (var i = 0; i < m.addedNodes.length; i++) file.push(m.addedNodes[i]);
        } else if (m.type === 'characterData') {
          file.push(m.target);
        }
      });
      if (timer) return;
      timer = setTimeout(function () {
        var lot = file; file = []; timer = null;
        lot.forEach(function (n) { if (n.isConnected) parcourir(n); });
      }, 50);
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', demarrer);
  else demarrer();
})();
