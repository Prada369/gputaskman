"""Desktop model cards preserve the collector's evidence and memory provenance."""
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from gputaskman.widget import device_readings, gpu_identity, local_model_names, memory, metric, model_cards, product_info, snapshot_source


class WidgetDataTests(unittest.TestCase):
    def test_b65_product_name_requires_the_exact_confirmed_board(self):
        hardware = dict(vendor='0x8086', device='0xe222', subsystem_vendor='0x1849', subsystem_device='0x6027')
        self.assertEqual(product_info(hardware)['name'], 'Intel Arc Pro B65')
        changed = dict(hardware, subsystem_device='0x9999')
        self.assertEqual(product_info(changed), {})

    def test_intel_readings_omit_irrelevant_nvidia_fields_and_explain_permissions(self):
        snapshot = {'adapter': {'vendor_id': 0x8086}, 'device': {
            'temp': 0, 'util_gpu': None, 'pcie_gen': None, 'compute_cap': '', 'pstate': None,
            'metric_status': {'util_gpu': 'perf access denied', 'pcie_gen': 'PCI capability read access required',
                              'compute_cap': 'NVIDIA-specific metric', 'pstate': 'NVIDIA-specific metric'}}}
        rows, unavailable = device_readings(snapshot)
        self.assertEqual(rows['GPU temperature'][0], '0°C')
        self.assertNotIn('GPU load', rows)
        self.assertEqual(unavailable['GPU load'], 'perf access denied')
        self.assertNotIn('P-state', unavailable)
        self.assertNotIn('CUDA capability', unavailable)
        self.assertFalse(any(value[0] == 'n/a' for value in rows.values()))

    def test_published_specs_never_supply_missing_live_link_or_bandwidth(self):
        hardware = dict(vendor='0x8086', device='0xe222', subsystem_vendor='0x1849', subsystem_device='0x6027')
        rows, _ = device_readings({'hardware': hardware, 'adapter': {'vendor_id': 0x8086},
                                   'device': {'pcie_gen': None, 'mem_read_bps': None}})
        self.assertNotIn('Live PCIe generation', rows)
        self.assertNotIn('Memory read traffic', rows)
        self.assertIn('capability', product_info(hardware)['specs']['PCIe support'])

    def test_hardware_make_model_and_board_are_separate(self):
        result = gpu_identity({"gpu_index": 2, "gpu_id": "pci-a", "adapter": {"vendor_id": 0x8086},
                               "hardware": {"make": "Intel", "model": "Battlemage G31", "board_make": "ASRock"}})
        self.assertEqual((result["make"], result["model"], result["board_make"]),
                         ("Intel", "Battlemage G31", "ASRock"))
        self.assertEqual(result["index"], 2)

    def test_saved_model_names_are_not_active_model_cards(self):
        with patch('gputaskman.stores.ollama_names', return_value={"digest": ["qwen:7b", "llama:8b"]}):
            self.assertEqual(local_model_names(), ["llama:8b", "qwen:7b"])
        self.assertEqual(model_cards({"model_library": ["qwen:7b"], "processes": []}), [])

    def test_unknown_and_measured_zero_remain_distinct(self):
        self.assertEqual(metric(None, "%"), "n/a")
        self.assertEqual(metric(0, "%"), "0%")
        self.assertEqual(memory(None), "n/a")
        self.assertEqual(memory(0), "0.00 GiB")

    def test_installed_candidates_do_not_become_active_models(self):
        snapshot = {"processes": [{"kind": "app", "installed_models": [{"path": "/models/example.gguf"}]}]}
        self.assertEqual(model_cards(snapshot), [])

    def test_mapped_shards_use_folder_name_without_summing_files_as_vram(self):
        process = {"pid": 12, "kind": "AI", "name": "python", "vram_bytes": None,
                   "model_files": [{"path": f"/models/Qwen/model-{i}.safetensors", "size": 8 << 30,
                                    "sources": ["mapped"]} for i in range(4)]}
        card = model_cards({"processes": [process]})[0]
        self.assertEqual(card["name"], "Qwen")
        self.assertEqual(card["evidence"], "mapped files")
        self.assertIn("Process VRAM n/a", card["details"])
        self.assertIn("4 model file(s)", card["details"])
        self.assertNotIn("32.00 GiB", card["details"])

    def test_path_argument_does_not_claim_residency(self):
        process = {"kind": "AI", "model_files": [{"path": "/models/test.gguf", "sources": ["path-arg"]}]}
        card = model_cards({"processes": [process]})[0]
        self.assertIn("residency unconfirmed", card["evidence"])
        self.assertNotIn("mapped files", card["evidence"])

    def test_inferred_process_owner_stays_labeled(self):
        process = {"kind": "AI", "vram_bytes": None, "vram_inferred_bytes": 2 << 30}
        card = model_cards({"processes": [process]})[0]
        self.assertIn("2.00 GiB (inferred owner)", card["details"])

    def test_runtime_memory_is_separate_from_process_allocation(self):
        process = {"kind": "LLM", "vram_bytes": 0,
                   "llm": {"server": {"models": [{"name": "model-q4", "size_vram": 4 << 30, "ctx": 32768}]}}}
        card = model_cards({"processes": [process]})[0]
        self.assertIn("runtime API", card["evidence"])
        self.assertIn("Process VRAM 0.00 GiB", card["details"])
        self.assertIn("4.00 GiB VRAM (runtime)", card["details"])
        self.assertIn("32,768 context (runtime)", card["details"])

    def test_ai_runtime_does_not_invent_a_model(self):
        card = model_cards({"processes": [{"kind": "AI", "runtimes": ["PyTorch"]}]})[0]
        self.assertEqual(card["name"], "Model not identified yet")
        self.assertIn("model unknown", card["evidence"])

    def test_model_estimates_retain_label(self):
        process = {"kind": "LLM", "llm": {"segments": [
            {"kind": "kv", "label": "KV cache", "bytes": 1 << 30, "exact": False}]}}
        card = model_cards({"processes": [process]})[0]
        self.assertIn("KV cache: 1.00 GiB (estimated)", card["details"])


class MultiGpuTests(unittest.TestCase):
    def setUp(self):
        from gputaskman.dxgi import Adapter
        self.adapters = [Adapter(luid=i, name="Same model", vendor_id=0x8086, dedicated_vram=16 << 30,
                                 shared_system=0, software=False) for i in (100, 200)]
        self.created = []

    def factory(self, index, adapters, scan_budget):
        from gputaskman.collector import Snapshot
        collector = MagicMock()
        collector.counters = SimpleNamespace(drm=SimpleNamespace(scan_budget=scan_budget))
        collector.sample.return_value = Snapshot(taken=1000, adapter=adapters[index], adapters=adapters,
            nv=None, driver="test", cuda="", vram_total=16 << 30, vram_used=(index+1) << 30,
            shared_used=None)
        self.created.append(collector)
        return collector

    def test_same_name_gpus_stay_separate_and_are_never_totaled(self):
        with snapshot_source(discover=lambda: self.adapters, factory=self.factory) as sample:
            result = sample()
            self.assertEqual(len(result['gpus']), 2)
            self.assertNotEqual(result['gpus'][0]['gpu_id'], result['gpus'][1]['gpu_id'])
            self.assertEqual([s['vram_used'] for s in result['gpus']], [1 << 30, 2 << 30])
            self.assertNotIn('vram_used', result)
            self.assertEqual(sum(c.scan_budget for c in self.created), .15)
            self.assertEqual(sum(c.counters.drm.scan_budget for c in self.created), .15)
            sample()
            self.assertEqual(len(self.created), 2)
        for collector in self.created:
            collector.close.assert_called_once()

    def test_single_gpu_selection_uses_original_index(self):
        with snapshot_source(1, discover=lambda: self.adapters, factory=self.factory) as sample:
            result = sample()
            self.assertEqual([s['gpu_index'] for s in result['gpus']], [1])
            self.assertEqual(result['gpus'][0]['adapter']['luid'], 200)

    def test_one_failed_gpu_does_not_remove_healthy_gpu(self):
        with snapshot_source(discover=lambda: self.adapters, factory=self.factory) as sample:
            sample()
            self.created[0].sample.side_effect = OSError('offline')
            result = sample()
            self.assertIn('offline', result['gpus'][0]['error'])
            self.assertIsNone(result['gpus'][0]['device'])
            self.assertNotIn('error', result['gpus'][1])

    def test_disappeared_gpu_closes_only_its_collector(self):
        with patch('gputaskman.widget.time.monotonic', side_effect=[0, 11]):
            with snapshot_source(discover=lambda: self.adapters, factory=self.factory) as sample:
                sample()
                removed = self.created[0]
                self.adapters = self.adapters[1:]
                result = sample()
                self.assertEqual(len(result['gpus']), 1)
                removed.close.assert_called_once()
                self.assertEqual(result['gpus'][0]['adapter']['luid'], 200)
                self.assertEqual(self.created[1].scan_budget, .15)

    def test_no_gpus_has_a_reason_without_fabricated_hardware(self):
        with snapshot_source(discover=lambda: [], factory=self.factory) as sample:
            result = sample()
            self.assertEqual(result['gpus'], [])
            self.assertIn('No GPU', result['error'])
            self.assertEqual(self.created, [])


if __name__ == "__main__":
    unittest.main()
