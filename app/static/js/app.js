/**
 * EVE PI Manager - App JavaScript
 * MIT License. If you enjoy this project, ISK donations to DrNightmare are appreciated.
 */

'use strict';

const EVE_LANG_KEY = 'eve-lang';
const EVE_SUPPORTED_LANGS = (window.EVE_I18N && window.EVE_I18N.supportedLanguages) || ['de', 'en', 'zh-Hans'];
const EVE_TEXT_TRANSLATIONS = {
    'Dashboard': { en: 'Dashboard', 'zh-Hans': 'ä»ªè¡¨ç›˜' },
    'Skyhooks': { en: 'Skyhooks', 'zh-Hans': 'Skyhook' },
    'PI Chain Planner': { en: 'PI Chain Planner', 'zh-Hans': 'PI ç”Ÿäº§é“¾è§„åˆ’å™¨' },
    'System Analyzer': { en: 'System Analyzer', 'zh-Hans': 'æ˜Ÿç³»åˆ†æžå™¨' },
    'System Mix': { en: 'System Mix', 'zh-Hans': 'æ˜Ÿç³»ç»„åˆ' },
    'Vergleich': { en: 'Compare', 'zh-Hans': 'æ¯”è¾ƒ' },
    'Jita Markt': { en: 'Jita Market', 'zh-Hans': 'å‰ä»–å¸‚åœº' },
    'Corporation': { en: 'Corporation', 'zh-Hans': 'å†›å›¢' },
    'Manager': { en: 'Manager', 'zh-Hans': 'ç®¡ç†é¢æ¿' },
    'Manager werden': { en: 'Become Manager', 'zh-Hans': 'æˆä¸ºç»ç†' },
    'Charaktere verwalten': { en: 'Manage Characters', 'zh-Hans': 'ç®¡ç†è§’è‰²' },
    'Alt hinzufuegen': { en: 'Add Alt', 'zh-Hans': 'æ·»åŠ å°å·' },
    'Abmelden': { en: 'Log out', 'zh-Hans': 'ç™»å‡º' },
    'Theme wechseln': { en: 'Toggle theme', 'zh-Hans': 'åˆ‡æ¢ä¸»é¢˜' },
    'Extractor-Benachrichtigungen': { en: 'Extractor notifications', 'zh-Hans': 'æå–å™¨é€šçŸ¥' },
    'Powered by EVE ESI': { en: 'Powered by EVE ESI', 'zh-Hans': 'ç”± EVE ESI é©±åŠ¨' },
    'Sprache wechseln': { en: 'Change language', 'zh-Hans': 'åˆ‡æ¢è¯­è¨€' },
    'Planetary Industry Dashboard': { en: 'Planetary Industry Dashboard', 'zh-Hans': 'è¡Œæ˜Ÿå·¥ä¸šä»ªè¡¨ç›˜' },
    'PI Chains Visualisierung': { en: 'PI chain visualization', 'zh-Hans': 'PI ç”Ÿäº§é“¾å¯è§†åŒ–' },
    'System Analyzer fuer optimale Planeten': { en: 'System analyzer for optimal planets', 'zh-Hans': 'ç”¨äºŽå¯»æ‰¾æœ€ä½³æ˜Ÿçƒçš„æ˜Ÿç³»åˆ†æžå™¨' },
    'Jita Marktpreise in Echtzeit': { en: 'Real-time Jita market prices', 'zh-Hans': 'å®žæ—¶å‰ä»–å¸‚åœºä»·æ ¼' },
    'Main & Alt Verwaltung': { en: 'Main & alt management', 'zh-Hans': 'ä¸»å·ä¸Žå°å·ç®¡ç†' },
    'Kolonien-Timer & Uebersicht': { en: 'Colony timers & overview', 'zh-Hans': 'æ®–æ°‘åœ°è®¡æ—¶å™¨ä¸Žæ€»è§ˆ' },
    'Mit EVE Online anmelden': { en: 'Sign in with EVE Online', 'zh-Hans': 'ä½¿ç”¨ EVE Online ç™»å½•' },
    'Der erste Account erhaelt automatisch Manager-Rechte.': { en: 'The first account automatically receives manager access.', 'zh-Hans': 'ç¬¬ä¸€ä¸ªè´¦æˆ·ä¼šè‡ªåŠ¨èŽ·å¾—ç»ç†æƒé™ã€‚' },
    'Zugang verweigert.': { en: 'Access denied.', 'zh-Hans': 'è®¿é—®è¢«æ‹’ç»ã€‚' },
    'Deine Korporation oder Allianz ist fuer dieses Tool nicht zugelassen. Wende dich an den Administrator.': { en: 'Your corporation or alliance is not approved for this tool. Please contact the administrator.', 'zh-Hans': 'ä½ çš„å†›å›¢æˆ–è”ç›Ÿæœªè¢«æ‰¹å‡†ä½¿ç”¨æ­¤å·¥å…·ã€‚è¯·è”ç³»ç®¡ç†å‘˜ã€‚' },
    'Diese App nutzt die offizielle ESI API.': { en: 'This app uses the official ESI API.', 'zh-Hans': 'æ­¤åº”ç”¨ä½¿ç”¨å®˜æ–¹ ESI APIã€‚' },
    'Wenn dir das Tool gefaellt, freue ich mich ueber ISK-Spenden an DrNightmare.': { en: 'If you like the tool, ISK donations to DrNightmare are appreciated.', 'zh-Hans': 'å¦‚æžœä½ å–œæ¬¢è¿™ä¸ªå·¥å…·ï¼Œæ¬¢è¿Žå‘ DrNightmare æèµ  ISKã€‚' },
    'MIT Licensed.': { en: 'MIT Licensed.', 'zh-Hans': 'é‡‡ç”¨ MIT è®¸å¯è¯ã€‚' },
    'PI Manager': { en: 'PI Manager', 'zh-Hans': 'PI ç®¡ç†å™¨' },
    'Zurueck zu meinem Account': { en: 'Back to my account', 'zh-Hans': 'è¿”å›žæˆ‘çš„è´¦æˆ·' },
    'Main': { en: 'Main', 'zh-Hans': 'ä¸»å·' },
    'Administrator': { en: 'Administrator', 'zh-Hans': 'ç®¡ç†å‘˜' },
    'Keine Korporation': { en: 'No corporation', 'zh-Hans': 'æ— å†›å›¢' },
    'Fuer Director-Zugriff fehlt der Corp-Role-Scope.': { en: 'The corporation role scope is required for director access.', 'zh-Hans': 'è‘£äº‹è®¿é—®éœ€è¦å†›å›¢è§’è‰² scopeã€‚' },
    'Jetzt aktualisieren': { en: 'Update now', 'zh-Hans': 'ç«‹å³æ›´æ–°' },
    'Kein Main': { en: 'No main', 'zh-Hans': 'æ— ä¸»å·' },
    'Letzte Preisaktualisierung wird geladen': { en: 'Loading last price update', 'zh-Hans': 'æ­£åœ¨åŠ è½½ä¸Šæ¬¡ä»·æ ¼æ›´æ–°æ—¶é—´' },
    'Preisaktualisierung': { en: 'Price update', 'zh-Hans': 'ä»·æ ¼æ›´æ–°' },
    'PI Kolonien': { en: 'PI Colonies', 'zh-Hans': 'PI æ®–æ°‘åœ°' },
    'ISK / Tag': { en: 'ISK / Day', 'zh-Hans': 'ISK / å¤©' },
    'Charaktere': { en: 'Characters', 'zh-Hans': 'è§’è‰²' },
    'Aktiv': { en: 'Active', 'zh-Hans': 'æ´»è·ƒ' },
    'Inaktiv': { en: 'Inactive', 'zh-Hans': 'éžæ´»è·ƒ' },
    'Alle Charaktere': { en: 'All characters', 'zh-Hans': 'å…¨éƒ¨è§’è‰²' },
    'Gerade aktualisiert': { en: 'Updated just now', 'zh-Hans': 'åˆšåˆšæ›´æ–°' },
    'Vor': { en: 'Ago', 'zh-Hans': 'å‰' },
    'Daten aktualisieren (max. 1Ã— pro Minute)': { en: 'Refresh data (max once per minute)', 'zh-Hans': 'åˆ·æ–°æ•°æ®ï¼ˆæ¯åˆ†é’Ÿæœ€å¤šä¸€æ¬¡ï¼‰' },
    'Charakter': { en: 'Character', 'zh-Hans': 'è§’è‰²' },
    'Ort': { en: 'Location', 'zh-Hans': 'ä½ç½®' },
    'Typ': { en: 'Type', 'zh-Hans': 'ç±»åž‹' },
    'Stufe': { en: 'Level', 'zh-Hans': 'ç­‰çº§' },
    'Tier': { en: 'Tier', 'zh-Hans': 'å±‚çº§' },
    'Ablauf': { en: 'Expiry', 'zh-Hans': 'åˆ°æœŸ' },
    'Lager': { en: 'Storage', 'zh-Hans': 'ä»“å‚¨' },
    'Produkt': { en: 'Product', 'zh-Hans': 'äº§å“' },
    'Main Chars': { en: 'Main chars', 'zh-Hans': 'ä¸»å·è§’è‰²' },
    'Kolonien': { en: 'Colonies', 'zh-Hans': 'æ®–æ°‘åœ°' },
    'Corporation Colonies': { en: 'Corporation colonies', 'zh-Hans': 'å†›å›¢æ®–æ°‘åœ°' },
    'Main Chars, Charaktere, Kolonien, ISK/Tag': { en: 'Main chars, characters, colonies, ISK/day', 'zh-Hans': 'ä¸»å·ã€è§’è‰²ã€æ®–æ°‘åœ°ã€ISK/å¤©' },
    'PI Skills': { en: 'PI skills', 'zh-Hans': 'PI æŠ€èƒ½' },
    'Kartenansicht': { en: 'Card view', 'zh-Hans': 'å¡ç‰‡è§†å›¾' },
    'Listenansicht': { en: 'List view', 'zh-Hans': 'åˆ—è¡¨è§†å›¾' },
    'Keine Charaktere gefunden.': { en: 'No characters found.', 'zh-Hans': 'æœªæ‰¾åˆ°è§’è‰²ã€‚' },
    'Zuletzt online': { en: 'Last online', 'zh-Hans': 'ä¸Šæ¬¡åœ¨çº¿' },
    'Unbekannt': { en: 'Unknown', 'zh-Hans': 'æœªçŸ¥' },
    'Kein Main-Charakter festgelegt': { en: 'No main character set', 'zh-Hans': 'æœªè®¾ç½®ä¸»è§’è‰²' },
    'Fuege einen Charakter hinzu oder lege einen als Main fest.': { en: 'Add a character or set one as main.', 'zh-Hans': 'æ·»åŠ ä¸€ä¸ªè§’è‰²æˆ–å°†ä¸€ä¸ªè§’è‰²è®¾ä¸ºä¸»å·ã€‚' },
    'Charakter hinzufuegen': { en: 'Add character', 'zh-Hans': 'æ·»åŠ è§’è‰²' },
    'Naechster Ablauf': { en: 'Next expiry', 'zh-Hans': 'ä¸‹ä¸€ä¸ªåˆ°æœŸ' },
    'Abgelaufen': { en: 'Expired', 'zh-Hans': 'å·²è¿‡æœŸ' },
    'PI Kolonien': { en: 'PI colonies', 'zh-Hans': 'PI æ®–æ°‘åœ°' },
    'Jita 4-4 Marktpreise': { en: 'Jita 4-4 market prices', 'zh-Hans': 'Jita 4-4 å¸‚åœºä»·æ ¼' },
    'Aktualisieren': { en: 'Refresh', 'zh-Hans': 'åˆ·æ–°' },
    'Produkt suchen...': { en: 'Search product...', 'zh-Hans': 'æœç´¢äº§å“...' },
    'Produkte': { en: 'Products', 'zh-Hans': 'äº§å“' },
    'Spread': { en: 'Spread', 'zh-Hans': 'ä»·å·®' },
    'Trend 24h': { en: 'Trend 24h', 'zh-Hans': '24å°æ—¶è¶‹åŠ¿' },
    'Handel / Tag': { en: 'Trade / Day', 'zh-Hans': 'äº¤æ˜“ / å¤©' },
    'Trend 7T': { en: 'Trend 7D', 'zh-Hans': '7å¤©è¶‹åŠ¿' },
    'Handel / 7T': { en: 'Trade / 7D', 'zh-Hans': 'äº¤æ˜“ / 7å¤©' },
    'Keine Produkte fuer diesen Filter.': { en: 'No products for this filter.', 'zh-Hans': 'æ­¤ç­›é€‰æ¡ä»¶ä¸‹æ²¡æœ‰äº§å“ã€‚' },
    'Skyhook Inventar': { en: 'Skyhook inventory', 'zh-Hans': 'Skyhook åº“å­˜' },
    'Bestand direkt editieren und mit Enter oder Speichern bestaetigen': { en: 'Edit inventory directly and confirm with Enter or Save', 'zh-Hans': 'ç›´æŽ¥ç¼–è¾‘åº“å­˜ï¼Œå¹¶ç”¨å›žè½¦æˆ–ä¿å­˜ç¡®è®¤' },
    'Zurueck': { en: 'Back', 'zh-Hans': 'è¿”å›ž' },
    'Keine Kolonien geladen.': { en: 'No colonies loaded.', 'zh-Hans': 'æœªåŠ è½½æ®–æ°‘åœ°ã€‚' },
    'Oeffne zuerst das Dashboard um die Daten zu laden.': { en: 'Open the dashboard first to load the data.', 'zh-Hans': 'è¯·å…ˆæ‰“å¼€ä»ªè¡¨ç›˜ä»¥åŠ è½½æ•°æ®ã€‚' },
    'Alle Systeme': { en: 'All systems', 'zh-Hans': 'å…¨éƒ¨æ˜Ÿç³»' },
    'Alle Typen': { en: 'All types', 'zh-Hans': 'å…¨éƒ¨ç±»åž‹' },
    'Reset': { en: 'Reset', 'zh-Hans': 'é‡ç½®' },
    'Planet': { en: 'Planet', 'zh-Hans': 'è¡Œæ˜Ÿ' },
    'System': { en: 'System', 'zh-Hans': 'æ˜Ÿç³»' },
    'Max. Produkt': { en: 'Max product', 'zh-Hans': 'æœ€é«˜äº§å“' },
    'Skyhook Bestand': { en: 'Skyhook inventory', 'zh-Hans': 'Skyhook åº“å­˜' },
    'Wert (ISK)': { en: 'Value (ISK)', 'zh-Hans': 'ä»·å€¼ (ISK)' },
    'Speichern': { en: 'Save', 'zh-Hans': 'ä¿å­˜' },
    '+ Zeile': { en: '+ Row', 'zh-Hans': '+ è¡Œ' },
    'System suchen': { en: 'Search system', 'zh-Hans': 'æœç´¢æ˜Ÿç³»' },
    'Analysieren': { en: 'Analyze', 'zh-Hans': 'åˆ†æž' },
    'Vergleich hinzufuegen': { en: 'Add to compare', 'zh-Hans': 'åŠ å…¥æ¯”è¾ƒ' },
    'Verfuegbare P0 Ressourcen': { en: 'Available P0 resources', 'zh-Hans': 'å¯ç”¨çš„ P0 èµ„æº' },
    'Filter loeschen': { en: 'Clear filters', 'zh-Hans': 'æ¸…é™¤ç­›é€‰' },
    'PI Empfehlungen': { en: 'PI recommendations', 'zh-Hans': 'PI æŽ¨è' },
    'Inputs': { en: 'Inputs', 'zh-Hans': 'è¾“å…¥ææ–™' },
    'Planeten': { en: 'Planets', 'zh-Hans': 'æ˜Ÿçƒ' },
    'Keine Produkte fuer diesen Filter verfuegbar.': { en: 'No products available for this filter.', 'zh-Hans': 'æ­¤ç­›é€‰æ¡ä»¶ä¸‹æ— å¯ç”¨äº§å“ã€‚' },
    '24h': { en: '24h', 'zh-Hans': '24å°æ—¶' },
    '30T': { en: '30D', 'zh-Hans': '30å¤©' },
    'Favoriten': { en: 'Favorites', 'zh-Hans': 'æ”¶è—' },
    'Auswahl': { en: 'Selection', 'zh-Hans': 'é€‰æ‹©' },
    'P2-P4 Produkte': { en: 'P2-P4 products', 'zh-Hans': 'P2-P4 äº§å“' },
    'Noch keine Daten.': { en: 'No data yet.', 'zh-Hans': 'æš‚æ— æ•°æ®ã€‚' },
    'Keine Planetendaten gefunden.': { en: 'No planet data found.', 'zh-Hans': 'æœªæ‰¾åˆ°æ˜Ÿçƒæ•°æ®ã€‚' },
    'Benoetigte Planetentypen:': { en: 'Required planet types:', 'zh-Hans': 'æ‰€éœ€æ˜Ÿçƒç±»åž‹ï¼š' },
    'Jita Sell:': { en: 'Jita sell:', 'zh-Hans': 'Jita å–ä»·ï¼š' },
    'Jita Buy:': { en: 'Jita buy:', 'zh-Hans': 'Jita ä¹°ä»·ï¼š' },
    'Inputs:': { en: 'Inputs:', 'zh-Hans': 'è¾“å…¥ææ–™ï¼š' },
    'Charakter suchen': { en: 'Search character', 'zh-Hans': 'æœç´¢è§’è‰²' },
    'Corporation': { en: 'Corporation', 'zh-Hans': 'å†›å›¢' },
    'Allianz': { en: 'Alliance', 'zh-Hans': 'è”ç›Ÿ' },
    'Token': { en: 'Token', 'zh-Hans': 'ä»¤ç‰Œ' },
    'Admin': { en: 'Manager', 'zh-Hans': 'ç»ç†' },
    'Besitzer': { en: 'Administrator', 'zh-Hans': 'ç®¡ç†å‘˜' },
    'Owner': { en: 'Administrator', 'zh-Hans': 'ç®¡ç†å‘˜' },
    'Administratoren': { en: 'Administrators', 'zh-Hans': 'ç®¡ç†å‘˜' },
    'Main chars': { en: 'Main chars', 'zh-Hans': 'ä¸»å·è§’è‰²' },
    'Chars': { en: 'Chars', 'zh-Hans': 'è§’è‰²' },
    'ISK/Tag': { en: 'ISK/day', 'zh-Hans': 'ISK/å¤©' },
    'Systeme': { en: 'Systems', 'zh-Hans': 'æ˜Ÿç³»' },
    'Konstellationen': { en: 'Constellations', 'zh-Hans': 'æ˜Ÿåº§' },
    'Top PI Empfehlungen': { en: 'Top PI recommendations', 'zh-Hans': 'PI æŽ¨è' },
    'Planetentypen im Vergleich': { en: 'Planet types in comparison', 'zh-Hans': 'å¯¹æ¯”ä¸­çš„æ˜Ÿçƒç±»åž‹' },
    'Noch keine Systeme ausgewaehlt.': { en: 'No systems selected yet.', 'zh-Hans': 'å°šæœªé€‰æ‹©æ˜Ÿç³»ã€‚' },
    'Noch keine Konstellationen ausgewaehlt.': { en: 'No constellations selected yet.', 'zh-Hans': 'å°šæœªé€‰æ‹©æ˜Ÿåº§ã€‚' },
    'Produkt suchen / auswaehlen': { en: 'Search / select product', 'zh-Hans': 'æœç´¢ / é€‰æ‹©äº§å“' },
    'WÃ¤hle ein Produkt um die vollstÃ¤ndige Produktionskette zu sehen.': { en: 'Choose a product to see the full production chain.', 'zh-Hans': 'é€‰æ‹©ä¸€ä¸ªäº§å“ä»¥æŸ¥çœ‹å®Œæ•´ç”Ÿäº§é“¾ã€‚' },
    'Waehle ein Produkt um die vollstaendige Produktionskette zu sehen.': { en: 'Choose a product to see the full production chain.', 'zh-Hans': 'é€‰æ‹©ä¸€ä¸ªäº§å“ä»¥æŸ¥çœ‹å®Œæ•´ç”Ÿäº§é“¾ã€‚' },
    'Alle P1â€“P4 Produkte werden unterstuetzt.': { en: 'All P1-P4 products are supported.', 'zh-Hans': 'æ”¯æŒæ‰€æœ‰ P1-P4 äº§å“ã€‚' },
    'Alle P1Ã¢â‚¬â€œP4 Produkte werden unterstÃƒÂ¼tzt.': { en: 'All P1-P4 products are supported.', 'zh-Hans': 'æ”¯æŒæ‰€æœ‰ P1-P4 äº§å“ã€‚' },
    'Auswahl loeschen': { en: 'Clear selection', 'zh-Hans': 'æ¸…é™¤é€‰æ‹©' },
    'System als Favorit speichern': { en: 'Save system as favorite', 'zh-Hans': 'å°†æ˜Ÿç³»ä¿å­˜ä¸ºæ”¶è—' },
    'Favorit': { en: 'Favorite', 'zh-Hans': 'æ”¶è—' },
    'Noch keine Charaktere ausgewaehlt.': { en: 'No characters selected yet.', 'zh-Hans': 'å°šæœªé€‰æ‹©è§’è‰²ã€‚' },
    'Mehrere Systeme und Konstellationen kombinieren und gemeinsame PI-Produkte sehen': { en: 'Combine multiple systems and constellations to see shared PI products', 'zh-Hans': '\u7ec4\u5408\u591a\u4e2a\u661f\u7cfb\u548c\u661f\u5ea7\uff0c\u67e5\u770b\u5171\u540c\u53ef\u751f\u4ea7\u7684 PI \u4ea7\u54c1' },
    'Ausgewaehlte Systeme': { en: 'Selected systems', 'zh-Hans': '\u5df2\u9009\u661f\u7cfb' },
    'AusgewÃ¤hlte Systeme': { en: 'Selected systems', 'zh-Hans': '\u5df2\u9009\u661f\u7cfb' },
    'Ausgewaehlte Konstellationen': { en: 'Selected constellations', 'zh-Hans': '\u5df2\u9009\u661f\u5ea7' },
    'AusgewÃ¤hlte Konstellationen': { en: 'Selected constellations', 'zh-Hans': '\u5df2\u9009\u661f\u5ea7' },
    'Waehle mindestens ein System oder eine Konstellation aus, um die kombinierte Produktion zu sehen.': { en: 'Choose at least one system or constellation to see combined production.', 'zh-Hans': '\u81f3\u5c11\u9009\u62e9\u4e00\u4e2a\u661f\u7cfb\u6216\u661f\u5ea7\u4ee5\u67e5\u770b\u7ec4\u5408\u751f\u4ea7\u7ed3\u679c\u3002' },
    'WÃ¤hle mindestens ein System oder eine Konstellation aus, um die kombinierte Produktion zu sehen.': { en: 'Choose at least one system or constellation to see combined production.', 'zh-Hans': '\u81f3\u5c11\u9009\u62e9\u4e00\u4e2a\u661f\u7cfb\u6216\u661f\u5ea7\u4ee5\u67e5\u770b\u7ec4\u5408\u751f\u4ea7\u7ed3\u679c\u3002' },
    'Teil einer Konstellation': { en: 'Part of a constellation', 'zh-Hans': '\u5c5e\u4e8e\u67d0\u4e2a\u661f\u5ea7' },
    'Direkt gewaehlt': { en: 'Directly selected', 'zh-Hans': '\u76f4\u63a5\u9009\u62e9' },
    'Direkt gewÃ¤hlt': { en: 'Directly selected', 'zh-Hans': '\u76f4\u63a5\u9009\u62e9' },
    'Keine Produkte fuer dieses Tier.': { en: 'No products for this tier.', 'zh-Hans': '\u6b64\u5c42\u7ea7\u6ca1\u6709\u4ea7\u54c1\u3002' },
    'Keine Produkte fÃ¼r dieses Tier.': { en: 'No products for this tier.', 'zh-Hans': '\u6b64\u5c42\u7ea7\u6ca1\u6709\u4ea7\u54c1\u3002' },
    'Keine passenden Planeten in der Auswahl gefunden.': { en: 'No matching planets found in the selection.', 'zh-Hans': '\u5f53\u524d\u9009\u62e9\u4e2d\u6ca1\u6709\u5339\u914d\u7684\u661f\u7403\u3002' },
    'Planetendaten werden geladen': { en: 'Loading planet data', 'zh-Hans': '\u6b63\u5728\u52a0\u8f7d\u661f\u7403\u6570\u636e' },
    'Systeme und Produkte werden gerade zusammengestellt.': { en: 'Systems and products are being compiled.', 'zh-Hans': '\u6b63\u5728\u6574\u7406\u661f\u7cfb\u548c\u4ea7\u54c1\u6570\u636e\u3002' },
    'Produkte P4': { en: 'Products P4', 'zh-Hans': 'P4 \u4ea7\u54c1' },
    'Produkte P3': { en: 'Products P3', 'zh-Hans': 'P3 \u4ea7\u54c1' },
    'Produkte P2': { en: 'Products P2', 'zh-Hans': 'P2 \u4ea7\u54c1' },
    'Details anzeigen': { en: 'Show details', 'zh-Hans': '\u663e\u793a\u8be6\u60c5' },
    'Details ausblenden': { en: 'Hide details', 'zh-Hans': '\u9690\u85cf\u8be6\u60c5' },
    'No data yet.': { en: 'No data yet.', 'zh-Hans': '\u6682\u65e0\u6570\u636e\u3002' },
    'No systems selected yet.': { en: 'No systems selected yet.', 'zh-Hans': '\u5c1a\u672a\u9009\u62e9\u661f\u7cfb\u3002' },
    'No constellations selected yet.': { en: 'No constellations selected yet.', 'zh-Hans': '\u5c1a\u672a\u9009\u62e9\u661f\u5ea7\u3002' },
    'Selection': { en: 'Selection', 'zh-Hans': '\u9009\u62e9' },
    'Planets': { en: 'Planets', 'zh-Hans': '\u661f\u7403' },
    'P2-P4 products': { en: 'P2-P4 products', 'zh-Hans': 'P2-P4 \u4ea7\u54c1' },
    'System Vergleich': { en: 'System Compare', 'zh-Hans': 'Ã¦ËœÅ¸Ã§Â³Â»Ã¦Â¯â€Ã¨Â¾Æ’' },
    'Keine Systeme zum Vergleich': { en: 'No systems to compare', 'zh-Hans': 'Ã¦Â²Â¡Ã¦Å“â€°Ã¥ÂÂ¯Ã¦Â¯â€Ã¨Â¾Æ’Ã§Å¡â€žÃ¦ËœÅ¸Ã§Â³Â»' },
    'Analysiere ein System im': { en: 'Analyze a system in', 'zh-Hans': 'Ã¨Â¯Â·Ã¥â€¦Ë†Ã¥Å“Â¨Ã¦Â­Â¤Ã¥Â¤â€žÃ¥Ë†â€ Ã¦Å¾ÂÃ¤Â¸â‚¬Ã¤Â¸ÂªÃ¦ËœÅ¸Ã§Â³Â»Ã¯Â¼Å¡' },
    'und klicke dort auf "Vergleich".': { en: 'and click "Compare" there.', 'zh-Hans': 'Ã§â€žÂ¶Ã¥ÂÅ½Ã§â€šÂ¹Ã¥â€¡Â»Ã¢â‚¬Å“Ã¦Â¯â€Ã¨Â¾Æ’Ã¢â‚¬ÂÃ£â‚¬â€š' },
    'Es koennen bis zu 4 Systeme gleichzeitig verglichen werden.': { en: 'Up to 4 systems can be compared at the same time.', 'zh-Hans': 'Ã¦Å“â‚¬Ã¥Â¤Å¡Ã¥ÂÂ¯Ã¥ÂÅ’Ã¦â€”Â¶Ã¦Â¯â€Ã¨Â¾Æ’ 4 Ã¤Â¸ÂªÃ¦ËœÅ¸Ã§Â³Â»Ã£â‚¬â€š' },
    'Alle entfernen': { en: 'Remove all', 'zh-Hans': 'Ã§Â§Â»Ã©â„¢Â¤Ã¥â€¦Â¨Ã©Æ’Â¨' },
    'Planetentyp': { en: 'Planet type', 'zh-Hans': 'Ã¦ËœÅ¸Ã§ÂÆ’Ã§Â±Â»Ã¥Å¾â€¹' }
};

const EVE_ZH_OVERRIDES = {
    'Dashboard': '\u4eea\u8868\u76d8',
    'Skyhooks': 'Skyhooks',
    'PI Chain Planner': 'PI \u751f\u4ea7\u94fe\u89c4\u5212\u5668',
    'System Analyzer': '\u661f\u7cfb\u5206\u6790\u5668',
    'System Mix': '\u661f\u7cfb\u7ec4\u5408',
    'System Mix - EVE PI Manager': '\u661f\u7cfb\u7ec4\u5408 - EVE PI \u7ba1\u7406\u5668',
    'Vergleich': '\u6bd4\u8f83',
    'Jita Markt': '\u5409\u4ed6\u5e02\u573a',
    'Corporation': '\u519b\u56e2',
    'Manager': '\u7ecf\u7406',
    'Sprache wechseln': '\u5207\u6362\u8bed\u8a00',
    'Theme wechseln': '\u5207\u6362\u4e3b\u9898',
    'PI Manager': 'PI \u7ba1\u7406\u5668',
    'Dashboard - EVE PI Manager': '\u4eea\u8868\u76d8 - EVE PI \u7ba1\u7406\u5668',
    'System Analyzer - EVE PI Manager': '\u661f\u7cfb\u5206\u6790\u5668 - EVE PI \u7ba1\u7406\u5668',
    'PI Chain Planner - EVE PI Manager': 'PI \u751f\u4ea7\u94fe\u89c4\u5212\u5668 - EVE PI \u7ba1\u7406\u5668',
    'Manager - EVE PI Manager': '\u7ecf\u7406 - EVE PI \u7ba1\u7406\u5668',
    'Manager Panel': '\u7ecf\u7406\u9762\u677f',
    'Charaktere': '\u89d2\u8272',
    'PI Kolonien': 'PI \u690d\u6c11\u5730',
    'ISK / Tag': 'ISK / \u5929',
    'Charakter': '\u89d2\u8272',
    'Ort': '\u4f4d\u7f6e',
    'Typ': '\u7c7b\u578b',
    'Stufe': '\u7b49\u7ea7',
    'Tier': '\u5c42\u7ea7',
    'Ablauf': '\u5230\u671f',
    'Lager': '\u4ed3\u50a8',
    'Skyhook': 'Skyhook',
    'Sell': '\u5356\u51fa',
    'Buy': '\u4e70\u5165',
    'Split': '\u62c6\u5206',
    'Naechster Ablauf': '\u4e0b\u4e00\u4e2a\u5230\u671f',
    'Nächster Ablauf': '\u4e0b\u4e00\u4e2a\u5230\u671f',
    'NÃ¤chster Ablauf': '\u4e0b\u4e00\u4e2a\u5230\u671f',
    'Gerade aktualisiert': '\u521a\u521a\u66f4\u65b0',
    'Vor': '\u524d',
    'Aktiv': '\u6d3b\u8dc3',
    'Inaktiv': '\u4e0d\u6d3b\u8dc3',
    'Alle Charaktere': '\u5168\u90e8\u89d2\u8272',
    'Charaktere verwalten': '\u7ba1\u7406\u89d2\u8272',
    'Alt hinzufuegen': '\u6dfb\u52a0\u5c0f\u53f7',
    'Alt hinzufÃ¼gen': '\u6dfb\u52a0\u5c0f\u53f7',
    'Kein Main-Charakter festgelegt': '\u672a\u8bbe\u7f6e\u4e3b\u89d2\u8272',
    'Fuege einen Charakter hinzu oder lege einen als Main fest.': '\u6dfb\u52a0\u4e00\u4e2a\u89d2\u8272\u6216\u5c06\u4e00\u4e2a\u89d2\u8272\u8bbe\u4e3a\u4e3b\u53f7\u3002',
    'Füge einen Charakter hinzu oder lege einen als Main fest.': '\u6dfb\u52a0\u4e00\u4e2a\u89d2\u8272\u6216\u5c06\u4e00\u4e2a\u89d2\u8272\u8bbe\u4e3a\u4e3b\u53f7\u3002',
    'FÃ¼ge einen Charakter hinzu oder lege einen als Main fest.': '\u6dfb\u52a0\u4e00\u4e2a\u89d2\u8272\u6216\u5c06\u4e00\u4e2a\u89d2\u8272\u8bbe\u4e3a\u4e3b\u53f7\u3002',
    'Charakter hinzufuegen': '\u6dfb\u52a0\u89d2\u8272',
    'Charakter hinzufügen': '\u6dfb\u52a0\u89d2\u8272',
    'Charakter hinzufÃ¼gen': '\u6dfb\u52a0\u89d2\u8272',
    'Abgelaufen': '\u5df2\u8fc7\u671f',
    'Accounts': '\u8d26\u6237',
    'Account-Verwaltung': '\u8d26\u6237\u7ba1\u7406',
    'Char-Name suchenâ€¦': '\u641c\u7d22\u89d2\u8272\u540d...',
    'Char-Name suchen…': '\u641c\u7d22\u89d2\u8272\u540d...',
    'Nur Manager': '\u4ec5\u663e\u793a\u7ecf\u7406',
    'Nach Name': '\u6309\u540d\u79f0',
    'Chars': '\u89d2\u8272',
    'Du': '\u4f60',
    'Erstellt:': '\u521b\u5efa\u4e8e\uff1a',
    'Manager entfernen': '\u79fb\u9664\u7ecf\u7406',
    'Manager machen': '\u8bbe\u4e3a\u7ecf\u7406',
    'Ansehen': '\u67e5\u770b',
    'Eigener Account': '\u81ea\u5df1\u7684\u8d26\u6237',
    'Alle Charaktere anzeigen': '\u663e\u793a\u5168\u90e8\u89d2\u8272',
    'Portrait': '\u5934\u50cf',
    'Name': '\u540d\u79f0',
    'Korporation': '\u519b\u56e2',
    'Status': '\u72b6\u6001',
    'Aktion': '\u64cd\u4f5c',
    'Main': '\u4e3b\u53f7',
    'Alt': '\u5c0f\u53f7',
    'Als Main setzen': '\u8bbe\u4e3a\u4e3b\u53f7',
    'Charakter löschen': '\u5220\u9664\u89d2\u8272',
    'Charakter löschen?': '\u5220\u9664\u89d2\u8272\uff1f',
    'Keine Accounts vorhanden.': '\u6ca1\u6709\u53ef\u7528\u8d26\u6237\u3002',
    'Keine Accounts für diesen Filter.': '\u6b64\u7b5b\u9009\u6761\u4ef6\u4e0b\u6ca1\u6709\u8d26\u6237\u3002',
    'Keine Accounts fÃ¼r diesen Filter.': '\u6b64\u7b5b\u9009\u6761\u4ef6\u4e0b\u6ca1\u6709\u8d26\u6237\u3002',
    'Zugangspolitik': '\u8bbf\u95ee\u7b56\u7565',
    'Offen': '\u5f00\u653e',
    'Allowlist': '\u5141\u8bb8\u5217\u8868',
    'Blocklist': '\u5c01\u7981\u5217\u8868',
    'Nur Administrator': '\u4ec5\u9650\u7ba1\u7406\u5458',
    'Registrierungs-Modus': '\u6ce8\u518c\u6a21\u5f0f',
    'Nur erlaubte Corps/Allianzen': '\u4ec5\u5141\u8bb8\u7684\u519b\u56e2/\u8054\u76df',
    'Gesperrte Corps/Allianzen': '\u88ab\u5c01\u7981\u7684\u519b\u56e2/\u8054\u76df',
    'Alle EVE-Charaktere können sich registrieren.': '\u6240\u6709 EVE \u89d2\u8272\u90fd\u53ef\u4ee5\u6ce8\u518c\u3002',
    'Alle EVE-Charaktere kÃ¶nnen sich registrieren.': '\u6240\u6709 EVE \u89d2\u8272\u90fd\u53ef\u4ee5\u6ce8\u518c\u3002',
    'Nur Charaktere aus eingetragenen Korps/Allianzen dürfen sich': '\u53ea\u6709\u6765\u81ea\u5df2\u8bb0\u5f55\u519b\u56e2/\u8054\u76df\u7684\u89d2\u8272\u624d\u80fd',
    'Nur Charaktere aus eingetragenen Korps/Allianzen dÃ¼rfen sich': '\u53ea\u6709\u6765\u81ea\u5df2\u8bb0\u5f55\u519b\u56e2/\u8054\u76df\u7684\u89d2\u8272\u624d\u80fd',
    'neu': '\u65b0',
    'registrieren.': '\u6ce8\u518c\u3002',
    'Nur Charaktere aus eingetragenen Korps/Allianzen dürfen sich neu registrieren.': '\u53ea\u6709\u6765\u81ea\u5df2\u8bb0\u5f55\u519b\u56e2/\u8054\u76df\u7684\u89d2\u8272\u624d\u80fd\u65b0\u6ce8\u518c\u3002',
    'Nur Charaktere aus eingetragenen Korps/Allianzen dÃ¼rfen sich neu registrieren.': '\u53ea\u6709\u6765\u81ea\u5df2\u8bb0\u5f55\u519b\u56e2/\u8054\u76df\u7684\u89d2\u8272\u624d\u80fd\u65b0\u6ce8\u518c\u3002',
    'Nur Charaktere aus eingetragenen Korps/Allianzen dürfen sich neu registrieren. Bestehende Accounts sind davon nicht betroffen.': '\u53ea\u6709\u6765\u81ea\u5df2\u8bb0\u5f55\u519b\u56e2/\u8054\u76df\u7684\u89d2\u8272\u624d\u80fd\u65b0\u6ce8\u518c\u3002\u5df2\u5b58\u5728\u7684\u8d26\u6237\u4e0d\u53d7\u5f71\u54cd\u3002',
    'Nur Charaktere aus eingetragenen Korps/Allianzen dÃ¼rfen sich neu registrieren. Bestehende Accounts sind davon nicht betroffen.': '\u53ea\u6709\u6765\u81ea\u5df2\u8bb0\u5f55\u519b\u56e2/\u8054\u76df\u7684\u89d2\u8272\u624d\u80fd\u65b0\u6ce8\u518c\u3002\u5df2\u5b58\u5728\u7684\u8d26\u6237\u4e0d\u53d7\u5f71\u54cd\u3002',
    'Bestehende Accounts sind davon nicht betroffen.': '\u5df2\u5b58\u5728\u7684\u8d26\u6237\u4e0d\u53d7\u5f71\u54cd\u3002',
    'Charaktere aus eingetragenen Korps/Allianzen werden bei der Neuregistrierung gesperrt.': '\u6765\u81ea\u5df2\u8bb0\u5f55\u519b\u56e2/\u8054\u76df\u7684\u89d2\u8272\u5728\u65b0\u6ce8\u518c\u65f6\u4f1a\u88ab\u62d2\u7edd\u3002',
    'Korporation': '\u519b\u56e2',
    'Allianz': '\u8054\u76df',
    'Erlaubte Einträge': '\u5141\u8bb8\u6761\u76ee',
    'Erlaubte EintrÃ¤ge': '\u5141\u8bb8\u6761\u76ee',
    'Hinzufügen': '\u6dfb\u52a0',
    'HinzufÃ¼gen': '\u6dfb\u52a0',
    'Typ': '\u7c7b\u578b',
    'Name': '\u540d\u79f0',
    'EVE ID': 'EVE ID',
    'Füge unten Corps oder Allianzen hinzu.': '\u5728\u4e0b\u65b9\u6dfb\u52a0\u519b\u56e2\u6216\u8054\u76df\u3002',
    'FÃ¼ge unten Corps oder Allianzen hinzu.': '\u5728\u4e0b\u65b9\u6dfb\u52a0\u519b\u56e2\u6216\u8054\u76df\u3002',
    'Eintrag hinzufügen': '\u6dfb\u52a0\u6761\u76ee',
    'Eintrag hinzufÃ¼gen': '\u6dfb\u52a0\u6761\u76ee',
    'Corp- oder Allianz-Name suchen…': '\u641c\u7d22\u519b\u56e2\u6216\u8054\u76df\u540d\u79f0...',
    'Corp- oder Allianz-Name suchenâ€¦': '\u641c\u7d22\u519b\u56e2\u6216\u8054\u76df\u540d\u79f0...',
    'Suchen': '\u641c\u7d22',
    'Korporation': '\u519b\u56e2',
    'Die EVE Korporations- oder Allianz-ID findest du auf': '\u4f60\u53ef\u4ee5\u5728\u4ee5\u4e0b\u5730\u65b9\u627e\u5230 EVE \u519b\u56e2\u6216\u8054\u76df ID\uff1a',
    'Suche…': '\u641c\u7d22\u4e2d...',
    'Suche...': '\u641c\u7d22\u4e2d...',
    'Fehler bei der Suche.': '\u641c\u7d22\u65f6\u51fa\u9519\u3002',
    'PI-Potential eines Systems analysieren': '\u5206\u6790\u661f\u7cfb\u7684 PI \u6f5c\u529b',
    'System suchen': '\u641c\u7d22\u661f\u7cfb',
    'z.B. Jita, Amarr, Dodixie...': '\u4f8b\u5982 Jita\u3001Amarr\u3001Dodixie...',
    'Analysiere System...': '\u6b63\u5728\u5206\u6790\u661f\u7cfb...',
    'LÃ¤dt...': '\u52a0\u8f7d\u4e2d...',
    'Lädt...': '\u52a0\u8f7d\u4e2d...',
    'Analysieren': '\u5206\u6790',
    'Bitte System Ã¼ber die Suche eingeben.': '\u8bf7\u901a\u8fc7\u641c\u7d22\u8f93\u5165\u661f\u7cfb\u3002',
    'Bitte System über die Suche eingeben.': '\u8bf7\u901a\u8fc7\u641c\u7d22\u8f93\u5165\u661f\u7cfb\u3002',
    'Planetentypen gesamt': '\u661f\u7403\u7c7b\u578b\u603b\u89c8',
    'System hinzufuegen': '\u6dfb\u52a0\u661f\u7cfb',
    'Konstellation hinzufuegen': '\u6dfb\u52a0\u661f\u5ea7',
    'Favoriten': '\u6536\u85cf',
    'Auswahl': '\u9009\u62e9',
    'Selection': '\u9009\u62e9',
    'Planets': '\u661f\u7403',
    'P2-P4 products': 'P2-P4 \u4ea7\u54c1',
    'P2-P4 Produkte': 'P2-P4 \u4ea7\u54c1',
    'Ausgewaehlte Systeme': '\u5df2\u9009\u661f\u7cfb',
    'AusgewÃ¤hlte Systeme': '\u5df2\u9009\u661f\u7cfb',
    'Noch keine Systeme ausgewaehlt.': '\u5c1a\u672a\u9009\u62e9\u661f\u7cfb\u3002',
    'No systems selected yet.': '\u5c1a\u672a\u9009\u62e9\u661f\u7cfb\u3002',
    'Ausgewaehlte Konstellationen': '\u5df2\u9009\u661f\u5ea7',
    'AusgewÃ¤hlte Konstellationen': '\u5df2\u9009\u661f\u5ea7',
    'Noch keine Konstellationen ausgewaehlt.': '\u5c1a\u672a\u9009\u62e9\u661f\u5ea7\u3002',
    'No constellations selected yet.': '\u5c1a\u672a\u9009\u62e9\u661f\u5ea7\u3002',
    'Noch keine Daten.': '\u6682\u65e0\u6570\u636e\u3002',
    'No data yet.': '\u6682\u65e0\u6570\u636e\u3002',
    'Mehrere Systeme und Konstellationen kombinieren und gemeinsame PI-Produkte sehen': '\u7ec4\u5408\u591a\u4e2a\u661f\u7cfb\u548c\u661f\u5ea7\uff0c\u67e5\u770b\u5171\u540c\u53ef\u751f\u4ea7\u7684 PI \u4ea7\u54c1',
    'Waehle mindestens ein System oder eine Konstellation aus, um die kombinierte Produktion zu sehen.': '\u81f3\u5c11\u9009\u62e9\u4e00\u4e2a\u661f\u7cfb\u6216\u661f\u5ea7\u4ee5\u67e5\u770b\u7ec4\u5408\u751f\u4ea7\u7ed3\u679c\u3002',
    'WÃ¤hle mindestens ein System oder eine Konstellation aus, um die kombinierte Produktion zu sehen.': '\u81f3\u5c11\u9009\u62e9\u4e00\u4e2a\u661f\u7cfb\u6216\u661f\u5ea7\u4ee5\u67e5\u770b\u7ec4\u5408\u751f\u4ea7\u7ed3\u679c\u3002',
    'Teil einer Konstellation': '\u5c5e\u4e8e\u67d0\u4e2a\u661f\u5ea7',
    'Direkt gewaehlt': '\u76f4\u63a5\u9009\u62e9',
    'Direkt gewÃ¤hlt': '\u76f4\u63a5\u9009\u62e9',
    'Keine Produkte fuer dieses Tier.': '\u6b64\u5c42\u7ea7\u6ca1\u6709\u4ea7\u54c1\u3002',
    'Keine Produkte fÃ¼r dieses Tier.': '\u6b64\u5c42\u7ea7\u6ca1\u6709\u4ea7\u54c1\u3002',
    'Keine passenden Planeten in der Auswahl gefunden.': '\u5f53\u524d\u9009\u62e9\u4e2d\u6ca1\u6709\u5339\u914d\u7684\u661f\u7403\u3002',
    'Planetendaten werden geladen': '\u6b63\u5728\u52a0\u8f7d\u661f\u7403\u6570\u636e',
    'Systeme und Produkte werden gerade zusammengestellt.': '\u6b63\u5728\u6574\u7406\u661f\u7cfb\u548c\u4ea7\u54c1\u6570\u636e\u3002',
    'Produkte P4': 'P4 \u4ea7\u54c1',
    'Produkte P3': 'P3 \u4ea7\u54c1',
    'Produkte P2': 'P2 \u4ea7\u54c1',
    'Details anzeigen': '\u663e\u793a\u8be6\u60c5',
    'Details ausblenden': '\u9690\u85cf\u8be6\u60c5',
    'System Vergleich': '\u661f\u7cfb\u6bd4\u8f83',
    'System Vergleich - EVE PI Manager': '\u661f\u7cfb\u6bd4\u8f83 - EVE PI \u7ba1\u7406\u5668',
    'Keine Systeme zum Vergleich': '\u6ca1\u6709\u53ef\u7528\u4e8e\u6bd4\u8f83\u7684\u661f\u7cfb',
    'Analysiere ein System im': '\u8bf7\u5148\u5728',
    'und klicke dort auf "Vergleich".': '\u4e2d\u5206\u6790\u4e00\u4e2a\u661f\u7cfb\uff0c\u7136\u540e\u70b9\u51fb\u201c\u6bd4\u8f83\u201d\u3002',
    'Es koennen bis zu 4 Systeme gleichzeitig verglichen werden.': '\u6700\u591a\u53ef\u540c\u65f6\u6bd4\u8f83 4 \u4e2a\u661f\u7cfb\u3002',
    'Es können bis zu 4 Systeme gleichzeitig verglichen werden.': '\u6700\u591a\u53ef\u540c\u65f6\u6bd4\u8f83 4 \u4e2a\u661f\u7cfb\u3002',
    'Alle entfernen': '\u5168\u90e8\u79fb\u9664',
    'Planetentyp': '\u661f\u7403\u7c7b\u578b',
    'Top PI Empfehlungen (Top 5 nach Jita Sell)': '\u9876\u7ea7 PI \u63a8\u8350\uff08\u6309 Jita \u5356\u51fa\u4ef7\u524d 5\uff09',
    'Produktionsketten planen und Planetenbedarf ermitteln': '\u89c4\u5212\u751f\u4ea7\u94fe\u5e76\u8ba1\u7b97\u6240\u9700\u661f\u7403',
    'Produkt suchen / auswählen': '\u641c\u7d22 / \u9009\u62e9\u4ea7\u54c1',
    'Produkt suchen / auswaehlen': '\u641c\u7d22 / \u9009\u62e9\u4ea7\u54c1',
    'Alle': '\u5168\u90e8',
    'Auswahl löschen': '\u6e05\u9664\u9009\u62e9',
    'Auswahl loeschen': '\u6e05\u9664\u9009\u62e9',
    'Wähle ein Produkt um die vollständige Produktionskette zu sehen.': '\u9009\u62e9\u4e00\u4e2a\u4ea7\u54c1\u4ee5\u67e5\u770b\u5b8c\u6574\u751f\u4ea7\u94fe\u3002',
    'Alle P1-P4 Produkte werden unterstützt.': '\u652f\u6301\u6240\u6709 P1-P4 \u4ea7\u54c1\u3002',
    'Alle P1-P4 Produkte werden unterstuetzt.': '\u652f\u6301\u6240\u6709 P1-P4 \u4ea7\u54c1\u3002',
    'System eingeben...': '\u8f93\u5165\u661f\u7cfb...',
    'System entfernen': '\u79fb\u9664\u661f\u7cfb',
    'System Analyzer öffnen': '\u6253\u5f00\u661f\u7cfb\u5206\u6790\u5668',
    'System Analyzer oeffnen': '\u6253\u5f00\u661f\u7cfb\u5206\u6790\u5668',
    'Produktions-Graph': '\u751f\u4ea7\u56fe',
    'Planeten anklicken zum Filtern': '\u70b9\u51fb\u661f\u7403\u8fdb\u884c\u7b5b\u9009',
    'Benötigte Planetentypen': '\u6240\u9700\u661f\u7403\u7c7b\u578b',
    'Benoetigte Planetentypen': '\u6240\u9700\u661f\u7403\u7c7b\u578b',
    'P0 Rohstoffe': 'P0 \u539f\u6599',
    'Verfügbare P0 Ressourcen': '\u53ef\u7528\u7684 P0 \u8d44\u6e90',
    'Verfuegbare P0 Ressourcen': '\u53ef\u7528\u7684 P0 \u8d44\u6e90',
    'Filter löschen': '\u6e05\u9664\u7b5b\u9009',
    'Filter loeschen': '\u6e05\u9664\u7b5b\u9009',
    'Trend:': '\u8d8b\u52bf\uff1a',
    'Trend': '\u8d8b\u52bf',
    'Angebot': '\u4f9b\u5e94',
    'Keine Produkte für diesen Filter verfügbar.': '\u6b64\u7b5b\u9009\u6761\u4ef6\u4e0b\u6ca1\u6709\u53ef\u7528\u4ea7\u54c1\u3002',
    'Keine Produkte fuer diesen Filter verfuegbar.': '\u6b64\u7b5b\u9009\u6761\u4ef6\u4e0b\u6ca1\u6709\u53ef\u7528\u4ea7\u54c1\u3002',
    'Produktionsketten planen und Planetenbedarf ermitteln': '\u89c4\u5212\u751f\u4ea7\u94fe\u5e76\u8ba1\u7b97\u661f\u7403\u9700\u6c42',
    'Produkt suchen...': '\u641c\u7d22\u4ea7\u54c1...',
    'Keine Produkte für diesen Filter.': '\u6b64\u7b5b\u9009\u6761\u4ef6\u4e0b\u6ca1\u6709\u4ea7\u54c1\u3002',
    'Keine Produkte fuer diesen Filter.': '\u6b64\u7b5b\u9009\u6761\u4ef6\u4e0b\u6ca1\u6709\u4ea7\u54c1\u3002',
    'Accounts': '\u8d26\u6237',
    'Administrator': '\u7ba1\u7406\u5458',
    'PI Colonies': 'PI \u690d\u6c11\u5730',
    'Trade / Day': '\u4ea4\u6613 / \u5929',
    'Trade / 7D': '\u4ea4\u6613 / 7\u5929',
    'Jita 4-4 Marktpreise': 'Jita 4-4 \u5e02\u573a\u4ef7\u683c',
    'Scopes aktualisieren': '\u66f4\u65b0 Scopes',
    'Kachelansicht': '\u5361\u7247\u89c6\u56fe',
    'Listenansicht': '\u5217\u8868\u89c6\u56fe',
    'Kein Token': '\u65e0 Token',
    'Läuft ab:': '\u5230\u671f\u65f6\u95f4\uff1a',
    'Laeuft ab:': '\u5230\u671f\u65f6\u95f4\uff1a',
    'Aktiver Main': '\u5f53\u524d\u4e3b\u53f7',
    'Als Main setzen': '\u8bbe\u4e3a\u4e3b\u53f7',
    'Verknuepfe weitere EVE-Charaktere mit deinem Account': '\u5c06\u66f4\u591a EVE \u89d2\u8272\u5173\u8054\u5230\u4f60\u7684\u8d26\u6237',
    'Verknüpfe weitere EVE-Charaktere mit deinem Account': '\u5c06\u66f4\u591a EVE \u89d2\u8272\u5173\u8054\u5230\u4f60\u7684\u8d26\u6237',
    'PI Chain Planner â€“ EVE PI Manager': 'PI \u751f\u4ea7\u94fe\u89c4\u5212\u5668 - EVE PI \u7ba1\u7406\u5668',
    'Produkt suchen / auswÃ¤hlen': '\u641c\u7d22 / \u9009\u62e9\u4ea7\u54c1',
    'Auswahl lÃ¶schen': '\u6e05\u9664\u9009\u62e9',
    'WÃ¤hle ein Produkt um die vollstÃ¤ndige Produktionskette zu sehen.': '\u9009\u62e9\u4e00\u4e2a\u4ea7\u54c1\u4ee5\u67e5\u770b\u5b8c\u6574\u751f\u4ea7\u94fe\u3002',
    'Alle P1â€“P4 Produkte werden unterstÃ¼tzt.': '\u652f\u6301\u6240\u6709 P1-P4 \u4ea7\u54c1\u3002',
    'System Analyzer Ã¶ffnen': '\u6253\u5f00\u661f\u7cfb\u5206\u6790\u5668',
    'Planetenfilter zurÃ¼cksetzen': '\u91cd\u7f6e\u661f\u7403\u7b5b\u9009',
    'Bitte System Ã¼ber die Suche eingeben.': '\u8bf7\u901a\u8fc7\u641c\u7d22\u8f93\u5165\u661f\u7cfb\u3002',
    'System zum Vergleich hinzufÃ¼gen (max. 4)': '\u5c06\u661f\u7cfb\u52a0\u5165\u6bd4\u8f83\uff08\u6700\u591a 4 \u4e2a\uff09',
    'Vergleich hinzufÃ¼gen': '\u52a0\u5165\u6bd4\u8f83',
    'VerfÃ¼gbare P0 Ressourcen': '\u53ef\u7528\u7684 P0 \u8d44\u6e90',
    'Filter lÃ¶schen': '\u6e05\u9664\u7b5b\u9009',
    'Bestand direkt editieren und mit Enter oder Speichern bestÃ¤tigen': '\u76f4\u63a5\u7f16\u8f91\u5e93\u5b58\uff0c\u7136\u540e\u6309 Enter \u6216\u4fdd\u5b58\u786e\u8ba4',
    'ZurÃ¼ck': '\u8fd4\u56de',
    'Ã–ffne zuerst das Dashboard um die Daten zu laden.': '\u8bf7\u5148\u6253\u5f00 Dashboard \u4ee5\u52a0\u8f7d\u6570\u636e\u3002'
};

const EVE_EXTRA_TRANSLATIONS = {
    'EVE PI Manager - Anmelden': { en: 'EVE PI Manager - Sign In', 'zh-Hans': 'EVE PI 管理器 - 登录' },
    'EVE Online und alle zugehoerigen Marken sind Eigentum von CCP Games.': { en: 'EVE Online and all related trademarks are owned by CCP Games.', 'zh-Hans': 'EVE Online 及所有相关商标均归 CCP Games 所有。' },
    'Du siehst das Dashboard als': { en: 'You are viewing the dashboard as', 'zh-Hans': '你当前正在以此身份查看仪表盘：' },
    'Zurueck zu meinem Account': { en: 'Back to my account', 'zh-Hans': '返回我的账号' },
    'Noch keine Preisaktualisierung vorhanden': { en: 'No price update available yet', 'zh-Hans': '尚无价格更新' },
    'ESI wird geprueft...': { en: 'Checking ESI...', 'zh-Hans': '正在检查 ESI...' },
    'CEO': { en: 'CEO', 'zh-Hans': 'CEO' },
    'Director': { en: 'Director', 'zh-Hans': '总监' },
    'Corporation Mains': { en: 'Corporation mains', 'zh-Hans': '军团主号' },
    'PI Typen': { en: 'PI types', 'zh-Hans': 'PI 类型' },
    'Keine Mains gefunden.': { en: 'No mains found.', 'zh-Hans': '未找到主号。' },
    'Keine Corp-Daten verfuegbar.': { en: 'No corporation data available.', 'zh-Hans': '没有可用的军团数据。' },
    'Die Mitglieder muessen ihre Daten zuerst einmal ueber das Dashboard laden.': { en: 'Members need to load their data once via the dashboard first.', 'zh-Hans': '成员需要先通过仪表盘加载一次数据。' },
    'Produkt Suche': { en: 'Product search', 'zh-Hans': '产品搜索' },
    'Produkt suchen, z. B. Coolant': { en: 'Search product, e.g. Coolant', 'zh-Hans': '搜索产品，例如 Coolant' },
    'Produkt eingeben, um passende Mains und Planeten anzuzeigen.': { en: 'Enter a product to show matching mains and planets.', 'zh-Hans': '输入产品以显示匹配的主号和行星。' },
    'Kein passendes Produkt gefunden.': { en: 'No matching product found.', 'zh-Hans': '未找到匹配的产品。' },
    'Corporation - Alle Accounts laden': { en: 'Corporation - Load all accounts', 'zh-Hans': '军团 - 加载所有账号' },
    'Verbleibend:': { en: 'Remaining:', 'zh-Hans': '剩余：' },
    'Fertig - Seite neu laden': { en: 'Done - Reload page', 'zh-Hans': '完成 - 重新加载页面' },
    'Schliessen': { en: 'Close', 'zh-Hans': '关闭' },
    'Alle Corporation-Accounts neu laden': { en: 'Reload all corporation accounts', 'zh-Hans': '重新加载所有军团账号' },
    'Alle laden': { en: 'Load all', 'zh-Hans': '全部加载' },
    'Dem Main-Char fehlt der Scope': { en: 'The main character is missing the scope', 'zh-Hans': '主角色缺少此 scope' },
    'CEO-Zugriff funktioniert weiter, Director-Zugriff braucht diesen Scope.': { en: 'CEO access still works, director access requires this scope.', 'zh-Hans': 'CEO 访问仍可使用，总监访问需要此 scope。' },
    'Main Chars': { en: 'Main chars', 'zh-Hans': '主号角色' },
    'Account(s) sind noch nicht geladen. Ein berechtigter Account kann den Cache fuer diese Corp vorbereiten.': { en: 'Some account(s) are not loaded yet. An authorized account can prepare the cache for this corporation.', 'zh-Hans': '仍有账号尚未加载。授权账号可以为该军团预热缓存。' },
    'Skyhook bearbeiten': { en: 'Edit skyhook', 'zh-Hans': '编辑 Skyhook' },
    'Kolonie laeuft nicht - theoretischer Wert': { en: 'Colony is not running - theoretical value', 'zh-Hans': '殖民地未运行 - 理论值' },
    'Kolonie läuft nicht — theoretischer Wert': { en: 'Colony is not running - theoretical value', 'zh-Hans': '殖民地未运行 - 理论值' },
    'Stalled': { en: 'Stalled', 'zh-Hans': '已停滞' },
    'Läuft': { en: 'Running', 'zh-Hans': '运行中' },
    'Laeuft': { en: 'Running', 'zh-Hans': '运行中' },
    'leer': { en: 'empty', 'zh-Hans': '空' },
    'Produktionsketten planen und Planetenbedarf ermitteln': { en: 'Plan production chains and determine planet requirements', 'zh-Hans': '规划生产链并计算所需行星' },
    'Nur Favoriten anzeigen': { en: 'Show favorites only', 'zh-Hans': '仅显示收藏' },
    'Mit diesen Planeten nicht produzierbar': { en: 'Not producible with these planets', 'zh-Hans': '无法用这些行星生产' },
    'Eingaben': { en: 'Inputs', 'zh-Hans': '输入' },
    'Weiterverarbeitung': { en: 'Processing', 'zh-Hans': '后续加工' },
    'Planeten anklicken zum Filtern': { en: 'Click planets to filter', 'zh-Hans': '点击行星进行筛选' },
    'System eingeben...': { en: 'Enter system...', 'zh-Hans': '输入星系...' },
    'System entfernen': { en: 'Remove system', 'zh-Hans': '移除星系' },
    'Lädt...': { en: 'Loading...', 'zh-Hans': '加载中...' },
    'LÃ¤dt...': { en: 'Loading...', 'zh-Hans': '加载中...' },
    'Region:': { en: 'Region:', 'zh-Hans': '地区：' },
    'Security:': { en: 'Security:', 'zh-Hans': '安等：' },
    'Planeten:': { en: 'Planets:', 'zh-Hans': '行星：' },
    'Kacheln anklicken zum Filtern': { en: 'Click tiles to filter', 'zh-Hans': '点击卡片进行筛选' },
    'System hinzufügen': { en: 'Add system', 'zh-Hans': '添加星系' },
    'System hinzufuegen': { en: 'Add system', 'zh-Hans': '添加星系' },
    'Keine Systeme zum Vergleich': { en: 'No systems to compare', 'zh-Hans': '没有可比较的星系' },
    'Analysiere ein System im': { en: 'Analyze a system in', 'zh-Hans': '请先在' },
    'und klicke dort auf "Vergleich".': { en: 'and click "Compare" there.', 'zh-Hans': '中分析一个星系，然后点击“比较”。' },
    'Es koennen bis zu 4 Systeme gleichzeitig verglichen werden.': { en: 'Up to 4 systems can be compared at the same time.', 'zh-Hans': '最多可同时比较 4 个星系。' },
    'Es können bis zu 4 Systeme gleichzeitig verglichen werden.': { en: 'Up to 4 systems can be compared at the same time.', 'zh-Hans': '最多可同时比较 4 个星系。' },
    'Top PI Empfehlungen (Top 5 nach Jita Sell)': { en: 'Top PI recommendations (top 5 by Jita sell)', 'zh-Hans': 'PI 推荐（按 Jita 卖价前 5）' },
    'Bestand direkt editieren und mit Enter oder Speichern bestaetigen': { en: 'Edit inventory directly and confirm with Enter or Save', 'zh-Hans': '可直接编辑库存，并用 Enter 或保存确认' },
    'Bestand direkt editieren und mit Enter oder Speichern bestätigen': { en: 'Edit inventory directly and confirm with Enter or Save', 'zh-Hans': '可直接编辑库存，并用 Enter 或保存确认' },
    'Zurueck': { en: 'Back', 'zh-Hans': '返回' },
    'Zurück': { en: 'Back', 'zh-Hans': '返回' },
    'Öffne zuerst das Dashboard um die Daten zu laden.': { en: 'Open the dashboard first to load the data.', 'zh-Hans': '请先打开仪表盘以加载数据。' },
    'Oeffne zuerst das Dashboard um die Daten zu laden.': { en: 'Open the dashboard first to load the data.', 'zh-Hans': '请先打开仪表盘以加载数据。' },
    'Main-Charakter festgelegt': { en: 'Main character set', 'zh-Hans': '已设置主角色' },
    'Fuer Director-Zugriff fehlt der Corp-Role-Scope.': { en: 'The corporation role scope is required for director access.', 'zh-Hans': '总监访问需要军团角色 scope。' },
    'Für Director-Zugriff fehlt der Corp-Role-Scope.': { en: 'The corporation role scope is required for director access.', 'zh-Hans': '总监访问需要军团角色 scope。' },
    'Alt hinzufügen': { en: 'Add alt', 'zh-Hans': '添加小号' },
    'Alt hinzufuegen': { en: 'Add alt', 'zh-Hans': '添加小号' },
    'Füge einen Charakter hinzu oder lege einen als Main fest.': { en: 'Add a character or set one as main.', 'zh-Hans': '添加一个角色，或将其中一个设置为主号。' },
    'Character hinzufügen': { en: 'Add character', 'zh-Hans': '添加角色' },
    'Charakter hinzufügen': { en: 'Add character', 'zh-Hans': '添加角色' },
    'Charakter hinzufuegen': { en: 'Add character', 'zh-Hans': '添加角色' },
    'Nächster Ablauf': { en: 'Next expiry', 'zh-Hans': '下一个到期' },
    'Nächstes Ablauf': { en: 'Next expiry', 'zh-Hans': '下一个到期' },
    'Daten aktualisieren (max. 1× pro Minute)': { en: 'Refresh data (max once per minute)', 'zh-Hans': '刷新数据（每分钟最多一次）' },
    'Läuft': { en: 'Running', 'zh-Hans': '运行中' },
    'wirklich entfernen?': { en: 'really remove?', 'zh-Hans': '确定要移除吗？' },
    'Kein Token': { en: 'No token', 'zh-Hans': '无令牌' },
    'Laeuft ab:': { en: 'Expires:', 'zh-Hans': '到期：' },
    'Verknuepfe weitere EVE-Charaktere mit deinem Account': { en: 'Link more EVE characters to your account', 'zh-Hans': '将更多 EVE 角色关联到你的账号' },
    'Verknüpfe weitere EVE-Charaktere mit deinem Account': { en: 'Link more EVE characters to your account', 'zh-Hans': '将更多 EVE 角色关联到你的账号' },
    'Scope fehlt': { en: 'Scope missing', 'zh-Hans': '缺少 scope' },
    'aktualisieren': { en: 'refresh', 'zh-Hans': '更新' },
    'Manager Panel': { en: 'Manager panel', 'zh-Hans': '经理面板' },
    'Char-Name suchen...': { en: 'Search character name...', 'zh-Hans': '搜索角色名...' },
    'Char-Name suchen…': { en: 'Search character name...', 'zh-Hans': '搜索角色名...' },
    'Eigener Account': { en: 'Own account', 'zh-Hans': '自己的账号' },
    'Als dieser User einloggen (Impersonate)': { en: 'Log in as this user (impersonate)', 'zh-Hans': '以该用户身份登录（模拟登录）' },
    'Dashboard als': { en: 'Show dashboard as', 'zh-Hans': '以此身份查看仪表盘：' },
    'Manager-Status von': { en: 'Change manager status of', 'zh-Hans': '修改以下对象的经理状态：' },
    'ändern?': { en: 'change?', 'zh-Hans': '确定更改吗？' },
    'und alle zugehörigen Charaktere wirklich löschen?': { en: 'and all associated characters?', 'zh-Hans': '并删除所有关联角色吗？' },
    'und alle zugehÃ¶rigen Charaktere wirklich lÃ¶schen?': { en: 'and all associated characters?', 'zh-Hans': '并删除所有关联角色吗？' },
    'Charakter löschen': { en: 'Delete character', 'zh-Hans': '删除角色' },
    'Charakter loeschen': { en: 'Delete character', 'zh-Hans': '删除角色' },
    'Charakter wirklich löschen?': { en: 'Really delete character?', 'zh-Hans': '确定删除该角色吗？' },
    'Keine Accounts für diesen Filter.': { en: 'No accounts for this filter.', 'zh-Hans': '此筛选条件下没有账号。' },
    'Keine Accounts fuer diesen Filter.': { en: 'No accounts for this filter.', 'zh-Hans': '此筛选条件下没有账号。' },
    'Offen': { en: 'Open', 'zh-Hans': '开放' },
    'Nur erlaubte Corps/Allianzen': { en: 'Allowed corps/alliances only', 'zh-Hans': '仅允许的军团/联盟' },
    'Gesperrte Corps/Allianzen': { en: 'Blocked corps/alliances', 'zh-Hans': '被封禁的军团/联盟' },
    'Nur Administrator': { en: 'Administrators only', 'zh-Hans': '仅管理员' },
    'Bestehende Accounts sind davon nicht betroffen.': { en: 'Existing accounts are not affected.', 'zh-Hans': '现有账号不受影响。' },
    'Gesperrte Einträge': { en: 'Blocked entries', 'zh-Hans': '封禁条目' },
    'Einträge (aktiv wenn Modus geändert wird)': { en: 'Entries (active when mode changes)', 'zh-Hans': '条目（切换模式后生效）' },
    'Eintraege (aktiv wenn Modus geaendert wird)': { en: 'Entries (active when mode changes)', 'zh-Hans': '条目（切换模式后生效）' },
    'Hinzugefügt': { en: 'Added', 'zh-Hans': '添加时间' },
    'Hinzugefuegt': { en: 'Added', 'zh-Hans': '添加时间' },
    'Eintrag entfernen': { en: 'Remove entry', 'zh-Hans': '移除条目' },
    'Keine Einträge vorhanden.': { en: 'No entries available.', 'zh-Hans': '没有条目。' },
    'Keine Eintraege vorhanden.': { en: 'No entries available.', 'zh-Hans': '没有条目。' },
    'Füge unten Corps oder Allianzen hinzu.': { en: 'Add corps or alliances below.', 'zh-Hans': '在下方添加军团或联盟。' },
    'Suche & Hinzufügen': { en: 'Search & add', 'zh-Hans': '搜索并添加' },
    'Eintrag hinzufügen': { en: 'Add entry', 'zh-Hans': '添加条目' },
    'Eintrag hinzufuegen': { en: 'Add entry', 'zh-Hans': '添加条目' },
    'Corp- oder Allianz-Name suchen...': { en: 'Search corp or alliance name...', 'zh-Hans': '搜索军团或联盟名称...' },
    'Corp- oder Allianz-Name suchen…': { en: 'Search corp or alliance name...', 'zh-Hans': '搜索军团或联盟名称...' },
    'Die EVE Korporations- oder Allianz-ID findest du auf': { en: 'You can find the EVE corporation or alliance ID on', 'zh-Hans': '你可以在这里找到 EVE 军团或联盟 ID：' },
    'Favoriten': { en: 'Favorites', 'zh-Hans': '收藏' },
    'leer': { en: 'empty', 'zh-Hans': '空' },
    'Lager': { en: 'Storage', 'zh-Hans': '仓储' }
};

const EVE_PATTERN_TRANSLATIONS = [
    {
        pattern: /^ESI: VIP Modus \((.+) Spieler\)$/,
        values: {
            en: 'ESI: VIP mode ($1 players)',
            'zh-Hans': 'ESIï¼šVIP æ¨¡å¼ï¼ˆ$1 åçŽ©å®¶ï¼‰'
        }
    },
    {
        pattern: /^ESI: Online [·.] (.+) Spieler$/,
        values: {
            en: 'ESI: Online · $1 players',
            'zh-Hans': 'ESIï¼šåœ¨çº¿ Â· $1 åçŽ©å®¶'
        }
    },
    {
        pattern: /^ESI: Nicht erreichbar$/,
        values: {
            en: 'ESI: Unreachable',
            'zh-Hans': 'ESIï¼šä¸å¯ç”¨'
        }
    },
    {
        pattern: /^Vor (\d+) Min\.$/,
        values: {
            en: '$1 min ago',
            'zh-Hans': '$1 åˆ†é’Ÿå‰'
        }
    },
    {
        pattern: /^Vor (\d+)h$/,
        values: {
            en: '$1h ago',
            'zh-Hans': '$1 å°æ—¶å‰'
        }
    },
    {
        pattern: /^EVE ID: (.+)$/,
        values: {
            en: 'EVE ID: $1',
            'zh-Hans': 'EVE ç¼–å·ï¼š$1'
        }
    },
    {
        pattern: /^(\d+) Produkte$/,
        values: {
            en: '$1 products',
            'zh-Hans': '$1 ä¸ªäº§å“'
        }
    },
    {
        pattern: /^(\d+)× leer$/,
        values: {
            en: '$1× empty',
            'zh-Hans': '$1× 空'
        }
    },
    {
        pattern: /^(\d+)x leer$/,
        values: {
            en: '$1x empty',
            'zh-Hans': '$1x 空'
        }
    },
    {
        pattern: /^(\d+) × leer$/,
        values: {
            en: '$1 × empty',
            'zh-Hans': '$1 × 空'
        }
    }
];

function getCurrentLanguage() {
    const lang = localStorage.getItem(EVE_LANG_KEY) || document.documentElement.getAttribute('lang') || (window.EVE_I18N && window.EVE_I18N.lang) || 'de';
    return EVE_SUPPORTED_LANGS.includes(lang) ? lang : 'de';
}

function getCurrentCatalog() {
    return (window.EVE_I18N && window.EVE_I18N.catalog) || {};
}

function translateKey(key, params = {}, fallback = '') {
    const template = getCurrentCatalog()[key] || fallback || key;
    return String(template).replace(/\{(\w+)\}/g, (_, name) => {
        return params[name] != null ? String(params[name]) : '';
    });
}

window.eveTranslateKey = translateKey;

function repairMojibakeText(value) {
    if (value == null) return value;
    const text = String(value);
    if (!/[ÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ]/.test(text)) {
        return text;
    }
    try {
        const bytes = new Uint8Array(Array.from(text, ch => ch.charCodeAt(0) & 0xff));
        const fixed = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
        return fixed && !fixed.includes('\ufffd') ? fixed : text;
    } catch {
        return text;
    }
}

function normalizeTranslationKey(value) {
    const repaired = repairMojibakeText(value);
    return repaired
        .replace(/ä/g, 'ae')
        .replace(/ö/g, 'oe')
        .replace(/ü/g, 'ue')
        .replace(/Ä/g, 'Ae')
        .replace(/Ö/g, 'Oe')
        .replace(/Ü/g, 'Ue')
        .replace(/ß/g, 'ss');
}

function looksLikeBrokenChinese(value) {
    if (!value) return false;
    return /[ÃÂÆÐÑØÙÚÛÝÞßæøåçœž]|â€|Â·|ï¼|Ã§|Ã¥|Ã¦|Ã¤|Ã©/.test(String(value));
}

function translateValue(value, lang = getCurrentLanguage()) {
    if (!value) return value;
    if (typeof value === 'string' && value.startsWith('i18n:')) {
        return translateKey(value.slice(5));
    }
    const text = String(value);
    const repairedText = repairMojibakeText(text);
    if (lang === 'de') return repairedText;
    const leading = repairedText.match(/^\s*/)?.[0] || '';
    const trailing = repairedText.match(/\s*$/)?.[0] || '';
    const trimmed = repairedText.trim();
    const normalizedTrimmed = normalizeTranslationKey(trimmed);
    const wrap = translated => `${leading}${translated}${trailing}`;
    const extra = EVE_EXTRA_TRANSLATIONS[trimmed] || EVE_EXTRA_TRANSLATIONS[normalizedTrimmed];
    if (extra && extra[lang]) {
        return wrap(extra[lang]);
    }
    if (lang === 'zh-Hans' && EVE_ZH_OVERRIDES[trimmed]) {
        return wrap(EVE_ZH_OVERRIDES[trimmed]);
    }
    if (lang === 'zh-Hans' && EVE_ZH_OVERRIDES[normalizedTrimmed]) {
        return wrap(EVE_ZH_OVERRIDES[normalizedTrimmed]);
    }
    const direct = EVE_TEXT_TRANSLATIONS[trimmed] || EVE_TEXT_TRANSLATIONS[normalizedTrimmed];
    if (direct) {
        let translated = direct[lang];
        if (lang === 'zh-Hans') {
            translated = translated ? repairMojibakeText(translated) : '';
            if (!translated || looksLikeBrokenChinese(translated)) {
                translated = direct.en || translated;
            }
        }
        if (translated) {
            return wrap(translated);
        }
    }
    for (const entry of EVE_PATTERN_TRANSLATIONS) {
        const match = trimmed.match(entry.pattern);
        if (!match) continue;
        let translated = entry.values[lang];
        if (lang === 'zh-Hans') {
            translated = translated ? repairMojibakeText(translated) : '';
            if (!translated || looksLikeBrokenChinese(translated)) {
                translated = entry.values.en || translated;
            }
        }
        if (!translated) continue;
        return wrap(translated.replace(/\$(\d+)/g, (_, index) => match[Number(index)] || ''));
    }
    return repairedText;
}

function applyTranslations(root = document.body) {
    const lang = getCurrentLanguage();
    document.documentElement.setAttribute('lang', lang);

    if (document.title) {
        if (!document.documentElement.dataset.i18nTitle) {
            document.documentElement.dataset.i18nTitle = document.title;
        }
        document.title = translateValue(document.documentElement.dataset.i18nTitle, lang);
    }

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
            if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
            const parent = node.parentElement;
            if (!parent) return NodeFilter.FILTER_REJECT;
            if (['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(parent.tagName)) return NodeFilter.FILTER_REJECT;
            return NodeFilter.FILTER_ACCEPT;
        }
    });

    while (walker.nextNode()) {
        const node = walker.currentNode;
        if (node.__i18nOriginal === undefined) node.__i18nOriginal = node.nodeValue;
        node.nodeValue = translateValue(node.__i18nOriginal, lang);
    }

    const attrs = ['title', 'placeholder', 'aria-label'];
    const elements = [];
    if (root && root.nodeType === Node.ELEMENT_NODE) elements.push(root);
    if (root && root.querySelectorAll) elements.push(...root.querySelectorAll('*'));
    elements.forEach(el => {
        if (el.dataset && el.dataset.i18nKey) {
            el.textContent = translateKey(el.dataset.i18nKey);
        }
        attrs.forEach(attr => {
            const keyedAttr = el.dataset && el.dataset[`i18n${attr.charAt(0).toUpperCase()}${attr.slice(1)}Key`];
            if (keyedAttr) {
                el.setAttribute(attr, translateKey(keyedAttr));
                return;
            }
            const value = el.getAttribute && el.getAttribute(attr);
            if (!value) return;
            const originalAttrName = `data-i18n-original-${attr}`;
            if (!el.getAttribute(originalAttrName)) {
                el.setAttribute(originalAttrName, value);
            }
            el.setAttribute(attr, translateValue(el.getAttribute(originalAttrName), lang));
        });
    });

    document.querySelectorAll('.eve-language-option').forEach(option => {
        option.classList.toggle('active', option.dataset.lang === lang);
    });
}

function setCurrentLanguage(lang) {
    const next = EVE_SUPPORTED_LANGS.includes(lang) ? lang : 'de';
    localStorage.setItem(EVE_LANG_KEY, next);
    document.cookie = `eve_lang=${encodeURIComponent(next)}; path=/; max-age=${60 * 60 * 24 * 365}`;
    document.documentElement.setAttribute('lang', next);
    window.location.reload();
}

function initLanguageSelector() {
    document.querySelectorAll('.eve-language-option').forEach(option => {
        const labels = {
            de: translateKey('lang.de', {}, 'Deutsch'),
            en: translateKey('lang.en', {}, 'English'),
            'zh-Hans': translateKey('lang.zh-Hans', {}, '简体中文')
        };
        option.textContent = labels[option.dataset.lang] || option.textContent;
    });
    document.querySelectorAll('.eve-language-option').forEach(option => {
        option.addEventListener('click', () => setCurrentLanguage(option.dataset.lang));
    });
}

function observeTranslations() {
    if (window.__eveI18nObserver || !document.body) return;
    window.__eveI18nObserver = new MutationObserver(mutations => {
        for (const mutation of mutations) {
            mutation.addedNodes.forEach(node => {
                if (node.nodeType === Node.ELEMENT_NODE) applyTranslations(node);
                if (node.nodeType === Node.TEXT_NODE && node.parentElement) applyTranslations(node.parentElement);
            });
        }
    });
    window.__eveI18nObserver.observe(document.body, { childList: true, subtree: true });
}

// ============ ESI Status Check ============
(function checkESIStatus() {
    const dot = document.getElementById('esiStatusDot');
    const text = document.getElementById('esiStatusText');
    if (!dot || !text) return;

    fetch('https://esi.evetech.net/latest/status/?datasource=tranquility', {
        method: 'GET',
        headers: { 'Accept': 'application/json' }
    })
    .then(res => {
        if (!res.ok) throw new Error('ESI nicht erreichbar');
        return res.json();
    })
    .then(data => {
        const players = data.players || 0;
        const vip = data.vip || false;
        dot.classList.add('online');
        if (vip) {
            dot.classList.remove('online');
            dot.style.background = '#f4a300';
            text.textContent = translateKey('footer.esi_vip', { players: players.toLocaleString(getCurrentLanguage() === 'zh-Hans' ? 'zh-CN' : 'de-DE') }, `ESI: VIP Modus (${players})`);
        } else {
            text.textContent = translateKey('footer.esi_online', { players: players.toLocaleString(getCurrentLanguage() === 'zh-Hans' ? 'zh-CN' : 'de-DE') }, `ESI: Online · ${players}`);
        }
    })
    .catch(() => {
        dot.classList.add('offline');
        text.textContent = translateKey('footer.esi_offline', {}, 'ESI: Nicht erreichbar');
    });
})();

// ============ Auto-collapse navbar on mobile ============
document.addEventListener('DOMContentLoaded', function() {
    initLanguageSelector();
    applyTranslations();
    observeTranslations();

    const navLinks = document.querySelectorAll('.navbar-nav .nav-link:not(.dropdown-toggle)');
    const navCollapse = document.getElementById('navMain');

    if (navCollapse) {
        navLinks.forEach(link => {
            link.addEventListener('click', () => {
                if (window.innerWidth < 992) {
                    const bsCollapse = bootstrap.Collapse.getInstance(navCollapse);
                    if (bsCollapse) bsCollapse.hide();
                }
            });
        });
    }
});

// ============ Number Formatting ============
function formatISK(value) {
    if (!value || value === 0) return '--';
    if (value >= 1e12) return (value / 1e12).toFixed(2) + ' T';
    if (value >= 1e9) return (value / 1e9).toFixed(2) + ' B';
    if (value >= 1e6) return (value / 1e6).toFixed(2) + ' M';
    if (value >= 1e3) return (value / 1e3).toFixed(0) + ' K';
    return value.toFixed(2);
}

// ============ Toast Notifications ============
function showToast(message, type = 'info') {
    const colors = {
        info: 'var(--eve-accent)',
        success: 'var(--eve-green)',
        warning: 'var(--eve-gold)',
        error: 'var(--eve-red)',
    };
    const toast = document.createElement('div');
    toast.style.cssText = `
        position: fixed; bottom: 20px; right: 20px; z-index: 9999;
        background: var(--eve-bg-2); border: 1px solid ${colors[type] || colors.info};
        color: var(--eve-text); padding: 0.75rem 1.25rem; border-radius: 6px;
        font-size: 0.875rem; box-shadow: 0 4px 16px rgba(0,0,0,0.4);
        animation: fadeIn 0.2s ease;
        max-width: 320px;
    `;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ============ Portrait Error Fallback ============
document.querySelectorAll('img[onerror]').forEach(img => {
    img.addEventListener('error', function() {
        if (!this.dataset.errored) {
            this.dataset.errored = '1';
            this.src = '/static/img/default_char.svg';
        }
    });
});

// ============ Theme Toggle ============
function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    document.documentElement.setAttribute('data-bs-theme', next);
    localStorage.setItem('eve-theme', next);
    updateThemeIcon(next);
}

function updateThemeIcon(theme) {
    const icon = document.getElementById('themeIcon');
    if (!icon) return;
    icon.className = theme === 'light' ? 'bi bi-moon' : 'bi bi-sun';
}

document.addEventListener('DOMContentLoaded', function() {
    updateThemeIcon(document.documentElement.getAttribute('data-theme') || 'dark');
});

// ============ Colony Table: Sort + Filter + Paginate ============
document.addEventListener('DOMContentLoaded', function () {
    const table = document.getElementById('coloniesTable');
    if (!table) return;

    const tbody = table.querySelector('tbody');
    const rows  = Array.from(tbody.querySelectorAll('tr'));
    const badge = document.getElementById('colonyCountBadge');
    let sortCol = null;
    let sortAsc = true;

    const FILTER_KEY = 'eve_pi_colony_filter';

    const pager = EvePaginate('coloniesTable', {
        pageSize: 6,
        controlsId: 'coloniesTablePagination',
        onCount: n => { if (badge) badge.textContent = n; }
    });

    table.querySelectorAll('th.eve-sortable').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.dataset.col;
            sortAsc = sortCol === col ? !sortAsc : true;
            sortCol = col;
            table.querySelectorAll('th.eve-sortable').forEach(h => {
                h.classList.remove('sort-asc', 'sort-desc');
                const icon = h.querySelector('.eve-sort-icon');
                if (icon) icon.className = 'bi bi-chevron-expand eve-sort-icon';
            });
            th.classList.add(sortAsc ? 'sort-asc' : 'sort-desc');
            const sortIcon = th.querySelector('.eve-sort-icon');
            if (sortIcon) {
                sortIcon.className = sortAsc ? 'bi bi-chevron-up eve-sort-icon' : 'bi bi-chevron-down eve-sort-icon';
            }
            sortRows(col, sortAsc);
        });
    });

    function sortRows(col, asc) {
        const attrMap = {
            char: 'sortChar', planet: 'sortPlanet', type: 'sortType',
            level: 'sortLevel', tier: 'sortTier', expiry: 'sortExpiry', isk: 'sortIsk',
            storage: 'sortStorage'
        };
        const attr = attrMap[col];
        if (!attr) return;
        const sorted = [...rows].sort((a, b) => {
            let av = a.dataset[attr] || '';
            let bv = b.dataset[attr] || '';
            if (col === 'expiry' || col === 'isk' || col === 'level' || col === 'storage') {
                av = parseFloat(av) || 0;
                bv = parseFloat(bv) || 0;
                return asc ? av - bv : bv - av;
            }
            if (col === 'tier') {
                av = parseInt(av.replace('P', ''), 10) || 0;
                bv = parseInt(bv.replace('P', ''), 10) || 0;
                return asc ? av - bv : bv - av;
            }
            return asc ? av.localeCompare(bv) : bv.localeCompare(av);
        });
        sorted.forEach(r => tbody.appendChild(r));
        applyFilter();
    }

    const filterSelect = document.getElementById('charFilter');
    const activeCheck = document.getElementById('activeFilter');
    const expiredCheck = document.getElementById('expiredFilter');
    const stalledCheck = document.getElementById('stalledFilter');

    window.applyFilters = applyFilter;

    function applyFilter() {
        const charVal = filterSelect ? filterSelect.value : '';
        const onlyActive = activeCheck ? activeCheck.classList.contains('active') : false;
        const onlyExpired = expiredCheck ? expiredCheck.classList.contains('active') : false;
        const onlyStalled = stalledCheck ? stalledCheck.classList.contains('active') : false;
        const hasStateFilter = onlyActive || onlyExpired || onlyStalled;
        const matched = rows.filter(r => {
            const charOk = !charVal || r.dataset.char === charVal;
            const stateOk = !hasStateFilter || (
                (onlyActive && r.dataset.active === '1') ||
                (onlyExpired && r.dataset.expired === '1') ||
                (onlyStalled && r.dataset.stalled === '1')
            );
            return charOk && stateOk;
        });
        pager.applyFilter(matched);
        saveFilterState();
    }

    function saveFilterState() {
        try {
            localStorage.setItem(FILTER_KEY, JSON.stringify({
                char: filterSelect ? filterSelect.value : '',
                active: activeCheck ? activeCheck.classList.contains('active') : false,
                expired: expiredCheck ? expiredCheck.classList.contains('active') : false,
                stalled: stalledCheck ? stalledCheck.classList.contains('active') : false,
                sortCol,
                sortAsc,
            }));
        } catch (_) {}
    }

    function restoreFilterState() {
        try {
            const state = JSON.parse(localStorage.getItem(FILTER_KEY) || '{}');
            if (state.char && filterSelect) filterSelect.value = state.char;
            if (state.active && activeCheck) activeCheck.classList.add('active');
            if (state.expired && expiredCheck) expiredCheck.classList.add('active');
            if (state.stalled && stalledCheck) stalledCheck.classList.add('active');
            if (state.sortCol) {
                sortCol = state.sortCol;
                sortAsc = state.sortAsc !== false;
                const th = table.querySelector(`th[data-col="${sortCol}"]`);
                if (th) {
                    table.querySelectorAll('th.eve-sortable').forEach(h => {
                        h.classList.remove('sort-asc', 'sort-desc');
                        const icon = h.querySelector('.eve-sort-icon');
                        if (icon) icon.className = 'bi bi-chevron-expand eve-sort-icon';
                    });
                    th.classList.add(sortAsc ? 'sort-asc' : 'sort-desc');
                    const icon = th.querySelector('.eve-sort-icon');
                    if (icon) icon.className = sortAsc ? 'bi bi-chevron-up eve-sort-icon' : 'bi bi-chevron-down eve-sort-icon';
                    sortRows(sortCol, sortAsc);
                    return;
                }
            }
        } catch (_) {}
        applyFilter();
    }

    restoreFilterState();
});
