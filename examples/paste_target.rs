//! Test helper: a throwaway native window that takes focus and prints every
//! key it receives and its text after each change, as a paste target.
//! `--app-id org.kde.konsole` makes it look like a terminal to Jumpkut;
//! `--backdrop` makes a large gradient window to take screenshots over.

slint::slint! {
    export component Target inherits Window {
        title: "Jumpkut paste target (test)";
        in property <bool> backdrop;
        width: backdrop ? 2560px : 520px;
        height: backdrop ? 1440px : 160px;
        background: backdrop ? @linear-gradient(135deg, #1d99f3 0%, #7d3fd0 55%, #f67400 100%) : #3daee9;
        callback key(string, bool, bool);
        callback changed(string);
        forward-focus: input;
        FocusScope {
            capture-key-pressed(event) => {
                root.key(event.text, event.modifiers.control, event.modifiers.shift);
                reject
            }
            input := TextInput {
                x: 16px;
                y: 16px;
                width: parent.width - 32px;
                height: parent.height - 32px;
                color: white;
                font-size: 18px;
                wrap: word-wrap;
                single-line: false;
                edited => { root.changed(self.text); }
            }
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    slint::BackendSelector::new().select().unwrap();
    if let Some(id) = args
        .iter()
        .position(|a| a == "--app-id")
        .and_then(|i| args.get(i + 1))
    {
        slint::set_xdg_app_id(id.as_str()).unwrap();
    }
    let target = Target::new().unwrap();
    target.set_backdrop(args.iter().any(|a| a == "--backdrop"));
    target.on_key(|text, control, shift| {
        let shown: String = text
            .chars()
            .map(|c| if c.is_control() { '·' } else { c })
            .collect();
        println!("key {shown:?} ctrl={control} shift={shift}");
    });
    target.on_changed(|text| println!("text {:?}", text.as_str()));
    target.run().unwrap();
}
