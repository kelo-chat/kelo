import Foundation

/// What the server sends for `server/examples/packages/habit-tracker` (docs/nest-ui.md, "A complete example"), plus one of each other
/// component (the same fixtures as the Android tests).
enum NestFixtures {
    static let row1 = #"{"key":"a1","title":"Walk","subtitle":"park","trailing":"2026-11-09","icon":"check","image":"","action":{"id":"remove","type":"store_delete","confirm":false,"label":"Remove","item":"a1","data":"checkins"}}"#
    static let row2 = #"{"key":"a2","title":"Read","subtitle":"","trailing":"2026-11-08","icon":"check","image":"","action":{"id":"remove","type":"store_delete","confirm":false,"label":"Remove","item":"a2","data":"checkins"}}"#

    /// The habit tracker's tab: header, form (select + text), list with a remove action, an "ask Kelo" button.
    static let habits = """
    {"schema":1,"package":"habit-tracker","surface":"habits","place":"tab","title":"Habits","components":[
      {"type":"header","text":"Keep the streak going","subtitle":"2 check-ins so far"},
      {"type":"form","fields":[
        {"id":"habit","type":"select","label":"Habit","required":true,"placeholder":"","options":[
          {"value":"Walk","label":"Walk 30 minutes"},{"value":"Read","label":"Read 20 pages"},{"value":"Water","label":"Drink 2 liters of water"}]},
        {"id":"note","type":"text","label":"Note","required":false,"placeholder":"","max_length":200}],
       "submit":{"label":"Check in","action":{"id":"check_in","type":"store_add","confirm":false,"label":"Check in"}}},
      {"type":"list","rows":[\(row1),\(row2)],"empty":null},
      {"type":"button","label":"Ask Kelo for a weekly review","style":"secondary","action":{"id":"weekly","type":"agent","confirm":false,"label":"Weekly review"}}
    ],"errors":[{"data":"trackings","error":"Connect Example Parcels first."}],"fetched_at":1762600000.0}
    """

    /// The habit tracker's home card.
    static let card = """
    {"schema":1,"package":"habit-tracker","surface":"today","place":"home_card","title":"Habits today","components":[
      {"type":"stat","label":"Check-ins","value":"2","unit":"","caption":"Last: Walk on 2026-11-09","icon":"trophy"}],"errors":[],"fetched_at":1762600000.0}
    """

    /// One of each of the other components, a part of a newer schema, and a card with a tappable action.
    static let all = """
    {"schema":1,"package":"demo","surface":"all","place":"tab","title":"Everything","components":[
      {"type":"text","text":"A title","style":"title"},
      {"type":"text","text":"Some body text","style":"body"},
      {"type":"text","text":"A caption","style":"caption"},
      {"type":"text","text":"A label","style":"label"},
      {"type":"text","text":"","style":"body"},
      {"type":"stat","label":"Distance","value":"12.5","unit":"km","caption":"this week","icon":"fitness"},
      {"type":"list","rows":[],"empty":{"text":"Nothing here yet","icon":"sparkle"}},
      {"type":"chart","kind":"bar","label":"Steps","points":[{"x":"Mon","y":3},{"x":"Tue","y":5},{"x":"Wed","y":2}]},
      {"type":"chart","kind":"line","label":"Weight","points":[]},
      {"type":"image","src":"/api/nest/extensions/demo/image?src=x","alt":"A chart picture","height":120},
      {"type":"card","title":"Parcel","subtitle":"on its way","action":{"id":"track","type":"open_url","confirm":false,"label":"Track","url":"https://example.com/track"},
       "children":[{"type":"text","text":"Arrives Friday","style":"body"},
                   {"type":"button","label":"Cancel parcel","style":"danger","action":{"id":"cancel","type":"connector","confirm":true,"label":"Cancel"}}]},
      {"type":"empty","text":"Connect your account","icon":"link","action":{"id":"connect","type":"agent","confirm":false,"label":"Ask Kelo"}},
      {"type":"future_widget","anything":{"a":1}},
      {"type":"unsupported","text":"Update the app to see this part"}
    ],"errors":[],"fetched_at":1762600000.0}
    """

    /// GET /api/nest/extensions with a tab, a draft tab, a My section, a home card and a chat card.
    static let extensions = """
    {"schema":1,"max_tabs":2,"icons":["home","star"],"places":{
      "tab":[{"key":"habit-tracker/habits","package":"habit-tracker","surface":"habits","place":"tab","title":"Habits","icon":"check_circle",
              "package_name":"Habit tracker","author":"Example Author","data_url":"/api/nest/extensions/habit-tracker/data?surface=habits","shown":true},
             {"key":"reading/books","package":"reading","surface":"books","place":"tab","title":"Books","icon":"book","package_name":"Reading",
              "author":"","data_url":"/api/nest/extensions/reading/data?surface=books","shown":true,"draft":true}],
      "my_section":[],"home_card":[],"chat_card":[]}}
    """
}
