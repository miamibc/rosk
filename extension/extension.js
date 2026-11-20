// Rosk panel indicator: quick capture and today's tasks.
// Talks to the Rosk service over D-Bus; holds no data of its own.

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'io.github.miamibc.rosk';
const OBJECT_PATH = '/io/github/miamibc/rosk/Service';
const IFACE = `
<node>
  <interface name="io.github.miamibc.rosk.Service">
    <method name="QuickAdd"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
    <method name="Preview"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
    <method name="Due"><arg type="s" direction="out"/></method>
    <method name="Counts"><arg type="s" direction="out"/></method>
    <method name="Complete"><arg type="s" direction="in"/></method>
    <method name="Open"><arg type="s" direction="in"/></method>
    <method name="Sync"/>
    <signal name="Changed"><arg type="s"/></signal>
  </interface>
</node>`;
const ServiceProxy = Gio.DBusProxy.makeProxyWrapper(IFACE);

function chipLabel(text, kind) {
    const label = new St.Label({
        text,
        style_class: `rosk-chip ${kind ?? ''}`,
        y_align: Clutter.ActorAlign.CENTER,
    });
    return label;
}

const RoskIndicator = GObject.registerClass(
class RoskIndicator extends PanelMenu.Button {
    _init(ext) {
        super._init(0.5, 'Rosk', false);
        this._ext = ext;
        this._settings = ext.getSettings();
        this._previewSource = 0;
        this._proxy = null;
        this._signalId = 0;

        const box = new St.BoxLayout({style_class: 'panel-status-menu-box'});
        this._icon = new St.Icon({
            gicon: Gio.icon_new_for_string(`${ext.path}/icons/rosk-symbolic.svg`),
            style_class: 'system-status-icon',
        });
        this._count = new St.Label({
            text: '',
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'rosk-count',
            visible: false,
        });
        box.add_child(this._icon);
        box.add_child(this._count);
        this.add_child(box);
        this.menu.box.add_style_class_name('rosk-menu');

        this._buildMenu();
        this._connectService();
    }

    _buildMenu() {
        const entryItem = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const column = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        this._entry = new St.Entry({
            hint_text: _('Add a task…  tomorrow  #list   (n: note)'),
            can_focus: true,
            x_expand: true,
            style_class: 'rosk-entry',
        });
        this._entry.set_primary_icon(new St.Icon({icon_name: 'list-add-symbolic', style_class: 'popup-menu-icon'}));
        column.add_child(this._entry);
        this._chips = new St.BoxLayout({style_class: 'rosk-chips', visible: false});
        column.add_child(this._chips);
        entryItem.add_child(column);
        this.menu.addMenuItem(entryItem);

        const text = this._entry.clutter_text;
        text.connect('activate', () => this._add(false));
        text.connect('key-press-event', (_actor, event) => {
            const key = event.get_key_symbol();
            const shift = (event.get_state() & Clutter.ModifierType.SHIFT_MASK) !== 0;
            if ((key === Clutter.KEY_Return || key === Clutter.KEY_KP_Enter) && shift) {
                this._add(true);
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        text.connect('text-changed', () => this._schedulePreview());

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._dueSection = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._dueSection);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction(_('Open Rosk'), () => this._call('Open', ''));

        this.menu.connect('open-state-changed', (_menu, open) => {
            if (open) {
                this._refreshDue();
                GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                    global.stage.set_key_focus(this._entry);
                    return GLib.SOURCE_REMOVE;
                });
            }
        });
    }

    _connectService() {
        this._proxy = new ServiceProxy(Gio.DBus.session, BUS_NAME, OBJECT_PATH, (proxy, error) => {
            if (error) {
                console.log(`Rosk: service proxy failed: ${error}`);
                return;
            }
            this._refreshCounts();
        }, null, Gio.DBusProxyFlags.DO_NOT_AUTO_START_AT_CONSTRUCTION);
        this._signalId = this._proxy.connectSignal('Changed', (_proxy, _sender, [json]) => {
            this._showCounts(json);
            if (this.menu.isOpen)
                this._refreshDue();
        });
        this._ownerId = this._proxy.connect('notify::g-name-owner', () => this._refreshCounts());
    }

    async _call(method, ...args) {
        if (!this._proxy)
            return null;
        try {
            const result = await this._proxy[`${method}Async`](...args);
            return result?.[0] ?? null;
        } catch (e) {
            console.log(`Rosk: ${method} failed: ${e.message}`);
            return null;
        }
    }

    async _refreshCounts() {
        const json = await this._call('Counts');
        if (json)
            this._showCounts(json);
    }

    _showCounts(json) {
        let counts;
        try {
            counts = JSON.parse(json);
        } catch {
            return;
        }
        const show = this._settings.get_boolean('show-count') && counts.today > 0;
        this._count.text = show ? String(counts.today) : '';
        this._count.visible = show;
        if (counts.overdue > 0)
            this._count.add_style_class_name('overdue');
        else
            this._count.remove_style_class_name('overdue');
    }

    async _refreshDue() {
        const json = await this._call('Due');
        this._dueSection.removeAll();
        let items = [];
        try {
            items = JSON.parse(json ?? '[]');
        } catch {}
        const header = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        header.add_child(new St.Label({text: _('Today'), style_class: 'rosk-header'}));
        this._dueSection.addMenuItem(header);
        if (items.length === 0) {
            const empty = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
            empty.add_child(new St.Label({
                text: json === null ? _('Rosk is starting…') : _('Nothing due. Enjoy the day.'),
                style_class: 'rosk-empty',
            }));
            this._dueSection.addMenuItem(empty);
            return;
        }
        for (const item of items)
            this._dueSection.addMenuItem(this._taskItem(item));
    }

    _taskItem(item) {
        const row = new PopupMenu.PopupBaseMenuItem({style_class: 'rosk-task'});
        const check = new St.Button({
            style_class: 'rosk-check',
            child: new St.Icon({icon_name: 'radio-symbolic', style_class: 'popup-menu-icon'}),
            y_align: Clutter.ActorAlign.CENTER,
            can_focus: true,
        });
        check.connect('enter-event', () => {
            check.child.icon_name = 'object-select-symbolic';
        });
        check.connect('leave-event', () => {
            check.child.icon_name = 'radio-symbolic';
        });
        check.connect('clicked', async () => {
            check.child.icon_name = 'object-select-symbolic';
            label.clutter_text.set_markup(`<s>${GLib.markup_escape_text(item.title, -1)}</s>`);
            label.opacity = 128;
            await this._call('Complete', item.id);
        });
        row.add_child(check);
        const label = new St.Label({
            text: item.title,
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'rosk-task-title',
        });
        label.clutter_text.ellipsize = 3; // Pango.EllipsizeMode.END
        row.add_child(label);
        if (item.chip)
            row.add_child(chipLabel(item.chip, item.state));
        row.connect('activate', () => this._call('Open', item.id));
        return row;
    }

    _schedulePreview() {
        if (this._previewSource)
            GLib.source_remove(this._previewSource);
        this._previewSource = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 120, () => {
            this._previewSource = 0;
            this._preview();
            return GLib.SOURCE_REMOVE;
        });
    }

    async _preview() {
        const text = this._entry.get_text();
        this._chips.destroy_all_children();
        if (!text.trim()) {
            this._chips.visible = false;
            return;
        }
        const json = await this._call('Preview', text);
        if (!json || this._entry.get_text() !== text)
            return;
        const {chips} = JSON.parse(json);
        for (const c of chips)
            this._chips.add_child(chipLabel(c.text, c.kind));
        this._chips.visible = chips.length > 0;
    }

    async _add(keepOpen) {
        const text = this._entry.get_text().trim();
        if (!text)
            return;
        this._entry.set_text('');
        await this._call('QuickAdd', text);
        if (keepOpen)
            this._refreshDue();
        else
            this.menu.close();
    }

    destroy() {
        if (this._previewSource)
            GLib.source_remove(this._previewSource);
        if (this._proxy) {
            if (this._signalId)
                this._proxy.disconnectSignal(this._signalId);
            if (this._ownerId)
                this._proxy.disconnect(this._ownerId);
            this._proxy = null;
        }
        super.destroy();
    }
});

export default class RoskExtension extends Extension {
    enable() {
        this._indicator = new RoskIndicator(this);
        Main.panel.addToStatusArea(this.uuid, this._indicator);
        this._settings = this.getSettings();
        Main.wm.addKeybinding('toggle-rosk', this._settings,
            Meta.KeyBindingFlags.IGNORE_AUTOREPEAT,
            Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW,
            () => this._indicator.menu.toggle());
    }

    disable() {
        Main.wm.removeKeybinding('toggle-rosk');
        this._indicator?.destroy();
        this._indicator = null;
        this._settings = null;
    }
}
